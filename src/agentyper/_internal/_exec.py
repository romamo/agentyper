"""Built-in batch exec command: dispatch a JSONL stream to any subcommand in-process."""

from __future__ import annotations

import contextlib
import functools
import inspect
import io
import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_type_hints

import agentyper._internal._errors as _err
import agentyper._internal._output as _out
from agentyper._internal._app import _list_inner_type
from agentyper._internal._errors import ExitCode
from agentyper._internal._params import ArgumentInfo, OptionInfo
from agentyper._internal._schema import _GLOBAL_PARAMS
from rich.console import Console

if TYPE_CHECKING:
    from agentyper._internal._app import Agentyper, CommandInfo

_SKIP_PARAMS = _GLOBAL_PARAMS | {"ctx", "_ctx", "context", "dry_run"}
_TL_ATTRS = ("format_", "start_ms", "timeout_ms", "warnings", "pagination", "request_id")


def _resolve_cmd_info(app: Agentyper, parts: list[str]) -> CommandInfo | None:
    """Walk the app hierarchy to find CommandInfo for a dot-path command."""
    if not parts:
        return None
    head = parts[0]
    if len(parts) == 1 and head in app._commands:
        return app._commands[head]
    if head in app._sub_apps:
        return _resolve_cmd_info(app._sub_apps[head], parts[1:])
    return None


@functools.lru_cache(maxsize=None)
def _fn_meta(fn: Callable) -> tuple[dict[str, Any], inspect.Signature]:
    """Return (type_hints, signature) for *fn*, cached per function identity."""
    try:
        hints: dict[str, Any] = get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = {}
    return hints, inspect.signature(fn)


def _append_flag(out: list[str], flag: str, annotation: Any, value: Any) -> None:
    """Append one flag entry to *out*, handling bool flags and complex values."""
    if annotation is bool or isinstance(value, bool):
        if value:
            out.append(flag)
    elif isinstance(value, (dict, list)):
        out.extend([flag, json.dumps(value)])
    else:
        out.extend([flag, str(value)])


def _dict_to_flags(d: dict[str, Any]) -> list[str]:
    """Convert a dict to flag args unconditionally: {"open_date": "x"} → ["--open-date", "x"]."""
    args: list[str] = []
    for key, value in d.items():
        _append_flag(args, "--" + key.replace("_", "-"), str, value)
    return args


def _payload_to_cmd_args(fn: Callable, payload: dict[str, Any]) -> list[str]:
    """Convert a payload dict to CLI args matching the command's parameter declarations.

    Positional parameters (no default, or ArgumentInfo) become bare values.
    Option parameters (OptionInfo, or any plain default) become ``--flag value`` pairs.
    """
    hints, sig = _fn_meta(fn)
    known = set(sig.parameters)
    positionals: list[str] = []
    flags: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in _SKIP_PARAMS or pname not in payload:
            continue

        value = payload[pname]
        default = param.default
        annotation = hints.get(pname, str)

        if isinstance(default, OptionInfo):
            flag = default.param_decls[0] if default.param_decls else "--" + pname.replace("_", "-")
            _append_flag(flags, flag, annotation, value)
        elif isinstance(default, ArgumentInfo) or default is inspect.Parameter.empty or default is ...:
            if _list_inner_type(annotation) is not None and isinstance(value, list):
                positionals.extend(str(v) for v in value)
            else:
                positionals.append(str(value))
        else:
            _append_flag(flags, "--" + pname.replace("_", "-"), annotation, value)

    for key, value in payload.items():
        if key not in known and key not in _SKIP_PARAMS:
            _append_flag(flags, "--" + key.replace("_", "-"), str, value)

    return positionals + flags


def _capture_invoke(app: Agentyper, args: list[str]) -> tuple[str, str, int]:
    """Invoke *app* with *args* in-process; return (stdout, stderr, exit_code).

    Redirects sys.stdout/stderr and Rich consoles so each sub-invocation is
    fully isolated. Saves and restores thread-local output state so the
    enclosing exec invocation's metadata (format, warnings, request_id …) is
    not clobbered.
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0

    old_stdout, old_stderr = sys.stdout, sys.stderr
    old_console = _out._console
    old_err_console = _err._err_console
    saved = {attr: getattr(_out._local, attr, None) for attr in _TL_ATTRS}

    try:
        sys.stdout = stdout_buf  # type: ignore[assignment]
        sys.stderr = stderr_buf  # type: ignore[assignment]
        _out._console = Console(file=stdout_buf, highlight=False, markup=False)
        _err._err_console = Console(file=stderr_buf, stderr=True, highlight=False, markup=False)
        app(args)
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 0
    except BaseException as exc:  # noqa: BLE001
        exit_code = int(ExitCode.GENERAL_ERROR)
        stderr_buf.write(str(exc) + "\n")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        _out._console = old_console
        _err._err_console = old_err_console
        for attr, val in saved.items():
            setattr(_out._local, attr, val)

    return stdout_buf.getvalue(), stderr_buf.getvalue(), exit_code


def _line_error(lineno: int, error: str, *, ignore_errors: bool) -> None:
    """Emit an ARG_ERROR for a malformed line; exit unless ignore_errors."""
    _emit(lineno, cmd=None, ok=False, exit_code=int(ExitCode.ARG_ERROR), error=error)
    if not ignore_errors:
        sys.exit(int(ExitCode.ARG_ERROR))


def run_exec(
    app: Agentyper,
    *,
    ignore_errors: bool = False,
    dry_run: bool = False,
) -> None:
    """Read JSONL from stdin; dispatch each line to *app* in-process.

    Protocol per line::

        {"_cmd": "account.create", "name": "Assets:Bank", "open_date": "2024-01-01"}
        {"_cmd": "transaction.add", "_opts": {"draft": true}, "date": "2024-01-15"}

    Fields:
      _cmd   — dot-separated subcommand path ("account.create" → ``account create``)
      _opts  — optional per-line flag overrides (``{"draft": true}`` → ``--draft``)
      ...    — all other fields are mapped to positional or flag args based on the
               target command's signature

    Each processed line emits one JSON object to stdout::

        {"ok": true,  "exit_code": 0, "line": 1, "cmd": "account.create", "result": {...}}
        {"ok": false, "exit_code": 5, "line": 2, "cmd": "account.delete", "error": "..."}

    Flags:
      --ignore-errors  Continue after a line error instead of stopping.
      --dry-run        Forward ``--dry-run`` to each dispatched mutating command.
    """
    any_failed = False

    for lineno, raw in enumerate(sys.stdin, start=1):
        raw = raw.strip()
        if not raw:
            continue

        try:
            record: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            any_failed = True
            _line_error(lineno, str(exc), ignore_errors=ignore_errors)
            continue

        if not isinstance(record, dict):
            any_failed = True
            _line_error(lineno, "line is not a JSON object", ignore_errors=ignore_errors)
            continue

        cmd_path: str | None = record.get("_cmd")
        if not cmd_path:
            any_failed = True
            _line_error(lineno, "missing '_cmd' field", ignore_errors=ignore_errors)
            continue

        opts: dict[str, Any] = dict(record.get("_opts") or {})
        payload: dict[str, Any] = {k: v for k, v in record.items() if not k.startswith("_")}

        cmd_parts = cmd_path.split(".")
        cmd_info = _resolve_cmd_info(app, cmd_parts)

        if dry_run and cmd_info is not None and cmd_info.mutating:
            opts["dry_run"] = True

        payload_args = (
            _payload_to_cmd_args(cmd_info.fn, payload)
            if cmd_info is not None
            else _dict_to_flags(payload)
        )

        args = cmd_parts + _dict_to_flags(opts) + payload_args + ["--format", "json"]

        stdout_str, stderr_str, exit_code = _capture_invoke(app, args)

        ok = exit_code == 0
        any_failed = any_failed or not ok

        stripped = stdout_str.strip()
        result_data: Any = None
        if stripped:
            with contextlib.suppress(json.JSONDecodeError):
                result_data = json.loads(stripped)

        _emit(
            lineno,
            cmd=cmd_path,
            ok=ok,
            exit_code=exit_code,
            result=result_data,
            stderr=stderr_str.strip() or None,
        )

        if not ok and not ignore_errors:
            sys.exit(exit_code)

    if any_failed and ignore_errors:
        sys.exit(int(ExitCode.PARTIAL_FAILURE))


def _emit(
    lineno: int,
    *,
    cmd: str | None,
    ok: bool,
    exit_code: int,
    result: Any = None,
    error: str | None = None,
    stderr: str | None = None,
) -> None:
    out: dict[str, Any] = {"ok": ok, "exit_code": exit_code, "line": lineno}
    if cmd is not None:
        out["cmd"] = cmd
    if result is not None:
        out["result"] = result
    if error is not None:
        out["error"] = error
    if stderr is not None:
        out["stderr"] = stderr
    print(json.dumps(out))
