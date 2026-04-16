"""
Core Agentyper application class.

Provides:
- @app.command()   — register a function as a CLI subcommand
- @app.callback()  — register a root callback (invoked before any subcommand)
- app.add_agentyper() — nest a sub-app as a command group
- app()            — parse sys.argv and dispatch

Auto-injects on every command:
  --format {table,json,csv}   output format (default: table; auto: isatty)
  --schema                    print command JSON Schema and exit
  --yes / -y                  auto-confirm all confirm() calls
  --no                        auto-deny  all confirm() calls
  --answers / -a VALUE        JSON payload for all interactive answers
  -v                          verbose logging (INFO)
  -vv                         debug logging (DEBUG)
  --version                   show version string (if Agentyper(version=...) given)
"""

from __future__ import annotations

import argparse
import collections.abc
import contextlib
import dataclasses
import inspect
import json
import os
import signal
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import ValidationError as _PydanticValidationError

from agentyper._internal._errors import (
    EXIT_CODE_TABLE,
    EXIT_SUCCESS,
    ExitCode,
    ExitCodeEntry,
    format_pydantic_error,
)
from agentyper._internal._logging import configure_logging
from agentyper._internal._output import (
    clear_warnings,
    render_output,
    set_format,
    set_start_time,
    set_timeout_ms,
)
from agentyper._internal._params import (
    ArgumentInfo,
    OptionInfo,
    ResourceId,
    check_hallucination_patterns,
)
from agentyper._internal._schema import _GLOBAL_PARAMS, build_app_schema, fn_to_input_schema
from agentyper._internal._session import InteractiveSession, set_session

# ---------------------------------------------------------------------------
# Internal bookkeeping
# ---------------------------------------------------------------------------


_DangerLevel = str  # "safe" | "mutating" | "destructive"
_OptionPlacement = str  # "any" | "strict"

# Session-scoped idempotency cache: "{cmd_name}:{key}" → raw result dict (REQ-C-007)
_idempotency_cache: dict[str, dict[str, Any]] = {}


def _default_exit_codes(danger_level: _DangerLevel) -> dict[int, ExitCodeEntry]:
    """Return a sensible default exit_codes map based on danger level."""
    base = {
        ExitCode.SUCCESS: EXIT_CODE_TABLE[ExitCode.SUCCESS],
        ExitCode.GENERAL_ERROR: EXIT_CODE_TABLE[ExitCode.GENERAL_ERROR],
        ExitCode.ARG_ERROR: EXIT_CODE_TABLE[ExitCode.ARG_ERROR],
        ExitCode.NOT_FOUND: EXIT_CODE_TABLE[ExitCode.NOT_FOUND],
    }
    if danger_level in ("mutating", "destructive"):
        base[ExitCode.PARTIAL_FAILURE] = EXIT_CODE_TABLE[ExitCode.PARTIAL_FAILURE]
        base[ExitCode.PRECONDITION] = EXIT_CODE_TABLE[ExitCode.PRECONDITION]
        base[ExitCode.CONFLICT] = EXIT_CODE_TABLE[ExitCode.CONFLICT]
    return base


@dataclasses.dataclass
class CommandInfo:
    name: str
    fn: Callable
    help: str
    mutating: bool
    danger_level: _DangerLevel = "safe"
    option_placement: _OptionPlacement = "any"
    exit_codes: dict[int, ExitCodeEntry] = dataclasses.field(default_factory=dict)
    requires_editor: bool = False
    non_interactive_alternatives: list[str] = dataclasses.field(default_factory=list)
    timeout_ms: int | None = None  # None = inherit from app default

    def __post_init__(self) -> None:
        if not self.exit_codes:
            self.exit_codes = _default_exit_codes(self.danger_level)


def _is_ci() -> bool:
    return bool(os.getenv("CI") or os.getenv("GITHUB_ACTIONS") or os.getenv("JENKINS_URL"))


def _global_option_specs(*, has_version: bool) -> dict[str, bool]:
    """Return framework option strings mapped to whether they consume a value."""
    specs = {
        "-h": False,
        "--help": False,
        "--schema": False,
        "--format": True,
        "--yes": False,
        "-y": False,
        "--no": False,
        "--answers": True,
        "-a": True,
        "--timeout": True,
        "-v": False,
    }
    if has_version:
        specs["--version"] = False
    return specs


def _command_option_specs(cmd_info: CommandInfo) -> dict[str, bool]:
    """Return known option strings for a command, including framework-injected flags."""
    specs = _global_option_specs(has_version=False)

    hints = get_type_hints(cmd_info.fn)
    sig = inspect.signature(cmd_info.fn)
    for param_name, param in sig.parameters.items():
        if param_name in _GLOBAL_PARAMS or param_name in {
            "dry_run",
            "ctx",
            "_ctx",
            "context",
            "idempotency_key",
            "_timeout_ms",
        }:
            continue

        annotation = hints.get(param_name, str)
        default = param.default
        if isinstance(default, OptionInfo):
            decls = default.param_decls or (f"--{param_name.replace('_', '-')}",)
            consumes_value = not (annotation is bool or default.is_flag)
            for decl in decls:
                specs[decl] = consumes_value
            continue

        if isinstance(default, ArgumentInfo) or default in (inspect.Parameter.empty, ...):
            continue

        flag_name = f"--{param_name.replace('_', '-')}"
        specs[flag_name] = annotation is not bool

    if cmd_info.mutating:
        specs["--dry-run"] = False
        specs["--idempotency-key"] = True

    return specs


def _matches_option_token(token: str, option_specs: dict[str, bool]) -> bool:
    """Return True if token matches a known option string."""
    if token in option_specs:
        return True
    if token.startswith("--") and "=" in token:
        return token.split("=", 1)[0] in option_specs
    if "-v" in option_specs and token.startswith("-v") and set(token[1:]) == {"v"}:
        return True
    return False


def _option_consumes_value(token: str, option_specs: dict[str, bool]) -> bool:
    """Return whether a token's option form consumes the following argv token."""
    if token in option_specs:
        return option_specs[token]
    if token.startswith("--") and "=" in token:
        return False
    if "-v" in option_specs and token.startswith("-v") and set(token[1:]) == {"v"}:
        return False
    return False


def _find_command_token_index(
    raw_args: list[str],
    command_path: list[str],
    *,
    has_version: bool,
) -> int | None:
    """Locate the final command token in argv while skipping global flag values."""
    option_specs = _global_option_specs(has_version=has_version)
    path_index = 0
    last_match: int | None = None
    i = 0
    while i < len(raw_args) and path_index < len(command_path):
        token = raw_args[i]
        if token == command_path[path_index]:
            last_match = i
            path_index += 1
            i += 1
            continue
        if _matches_option_token(token, option_specs):
            i += 1
            if _option_consumes_value(token, option_specs):
                i += 1
            continue
        i += 1
    return last_match if path_index == len(command_path) else None


def _validate_strict_option_placement(
    raw_args: list[str],
    *,
    cmd_info: CommandInfo,
    command_path: list[str] | None,
    has_version: bool,
    parser: _Parser,
) -> None:
    """Reject known options after positional args for strict-placement commands."""
    if cmd_info.option_placement != "strict" or not command_path:
        return

    command_index = _find_command_token_index(raw_args, command_path, has_version=has_version)
    if command_index is None:
        return

    option_specs = _command_option_specs(cmd_info)
    seen_positional = False
    i = command_index + 1
    while i < len(raw_args):
        token = raw_args[i]
        if token == "--":
            return
        if _matches_option_token(token, option_specs):
            if seen_positional:
                parser.error(
                    "strict-placement commands require options before positional arguments"
                )
            i += 1
            if _option_consumes_value(token, option_specs):
                i += 1
            continue
        seen_positional = True
        i += 1


# ---------------------------------------------------------------------------
# Parser subclass — ARG_ERROR (3) on parse failures (REQ-F-002)
# ---------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with ARG_ERROR (3) instead of argparse's 2."""

    def error(self, message: str) -> None:
        if sys.stderr.isatty():
            self.print_usage(sys.stderr)
            self.exit(int(ExitCode.ARG_ERROR), f"{self.prog}: error: {message}\n")
        else:
            print(
                json.dumps(
                    {
                        "error": True,
                        "error_type": "Error",
                        "message": message,
                        "exit_code": int(ExitCode.ARG_ERROR),
                        "phase": "validation",
                    }
                ),
                file=sys.stderr,
            )
            self.exit(int(ExitCode.ARG_ERROR))


# ---------------------------------------------------------------------------
# Help action — routes help text to stderr in non-TTY (REQ-F-048)
# ---------------------------------------------------------------------------


class _HelpAction(argparse.Action):
    """Print help to stderr in non-TTY mode so stdout stays machine-parseable."""

    def __init__(
        self,
        option_strings: list[str],
        dest: str = argparse.SUPPRESS,
        default: str = argparse.SUPPRESS,
        help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            help=help,
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        out = sys.stdout if sys.stdout.isatty() else sys.stderr
        parser.print_help(out)
        parser.exit()


# ---------------------------------------------------------------------------
# Schema-print argparse action (eager: fires before required-arg validation)
# ---------------------------------------------------------------------------


class _SchemaPrintAction(argparse.Action):
    """Intercepts --schema before argument validation and prints JSON Schema."""

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        schema_fn: Callable[[], dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)
        self._schema_fn = schema_fn

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(json.dumps(self._schema_fn(), indent=2))
        parser.exit(EXIT_SUCCESS)


# ---------------------------------------------------------------------------
# Agentyper
# ---------------------------------------------------------------------------


class Agentyper:
    """
    Agent-first multi-command CLI application.

    Drop-in Typer replacement with built-in agent ergonomics:
    ``--schema``, ``--format``, ``--yes``, ``--no``, ``--answers``.

    Example::

        app = Agentyper(name="my-tool", version="1.0.0")

        @app.command()
        def search(ticker: str, limit: int = Option(10)):
            \"\"\"Search securities.\"\"\"
            results = service.search(ticker, limit)
            agentyper.output(results)

        app()
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        version: str | None = None,
        help: str | None = None,
        invoke_without_command: bool = False,
        default_timeout_ms: int = 0,
    ) -> None:
        self.name = name
        self.version = version
        self.help = help
        self.invoke_without_command = invoke_without_command
        self.default_timeout_ms = default_timeout_ms
        self._commands: dict[str, CommandInfo] = {}
        self._sub_apps: dict[str, Agentyper] = {}
        self._callback_fn: Callable | None = None

    # ------------------------------------------------------------------
    # Decorators
    # ------------------------------------------------------------------

    def command(
        self,
        name: str | None = None,
        *,
        help: str | None = None,
        mutating: bool = False,
        danger_level: _DangerLevel | None = None,
        option_placement: _OptionPlacement = "any",
        exit_codes: dict[int, ExitCodeEntry] | None = None,
        requires_editor: bool = False,
        non_interactive_alternatives: list[str] | None = None,
        timeout_ms: int | None = None,
    ) -> Callable:
        """
        Register a function as a CLI subcommand.

        Args:
            name:                         Override the command name.
            help:                         Override help text (defaults to docstring).
            mutating:                     Mark as write/mutation (auto-adds ``--dry-run``,
                                          ``--idempotency-key``).
            danger_level:                 ``"safe"``, ``"mutating"``, or ``"destructive"``.
                                          Inferred from ``mutating`` if omitted.
            exit_codes:                   Explicit exit code declarations (REQ-C-001).
                                          Framework defaults applied if omitted.
            requires_editor:              True if the command opens ``$EDITOR`` (REQ-C-023).
            non_interactive_alternatives: Flag names that bypass the editor (REQ-C-023),
                                          e.g. ``["message", "from-file"]``.
        """

        def decorator(fn: Callable) -> Callable:
            cmd_name = name or fn.__name__.rstrip("_").replace("_", "-")
            cmd_help = help or inspect.cleandoc(fn.__doc__ or "")
            _dl = danger_level or ("mutating" if mutating else "safe")
            self._commands[cmd_name] = CommandInfo(
                name=cmd_name,
                fn=fn,
                help=cmd_help,
                mutating=mutating,
                danger_level=_dl,
                option_placement=option_placement,
                exit_codes=exit_codes or {},
                requires_editor=requires_editor,
                non_interactive_alternatives=non_interactive_alternatives or [],
                timeout_ms=timeout_ms,
            )
            return fn

        return decorator

    def callback(self, *, invoke_without_command: bool = False) -> Callable:
        """Register a root-level callback (run before any subcommand)."""

        def decorator(fn: Callable) -> Callable:
            self._callback_fn = fn
            return fn

        return decorator

    def add_agentyper(self, agentyper: Agentyper, *, name: str) -> None:
        """
        Nest another :class:`Agentyper` app as a named command group.

        Mirrors ``typer.Typer.add_typer()``.
        """
        self._sub_apps[name] = agentyper

    def add_typer(self, agentyper: Agentyper, *, name: str) -> None:
        """Alias for :meth:`add_agentyper` for Typer drop-in compatibility."""
        self.add_agentyper(agentyper, name=name)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict[str, Any]:
        """Return the full JSON Schema for this app and all sub-apps."""
        return build_app_schema(
            name=self.name or "",
            version=self.version,
            commands=self._commands,
            sub_apps=self._sub_apps,
        )

    # ------------------------------------------------------------------
    # argparse builder
    # ------------------------------------------------------------------

    def _build_parser(self) -> argparse.ArgumentParser:
        return self._build_parser_internal(callbacks=[])

    def _build_parser_internal(self, callbacks: list[Callable]) -> argparse.ArgumentParser:
        parser = _Parser(
            prog=self.name,
            description=self.help,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            add_help=False,
        )
        self._inject_global_flags(parser, schema_fn=self.get_schema)

        my_callbacks = callbacks.copy()
        if self._callback_fn:
            my_callbacks.append(self._callback_fn)

        if self._commands or self._sub_apps:
            subparsers = parser.add_subparsers(
                dest="_command", metavar="COMMAND", parser_class=_Parser
            )
            subparsers.required = not self.invoke_without_command

            for cmd_name, cmd_info in self._commands.items():

                def _cmd_schema_fn(ci: CommandInfo = cmd_info) -> dict[str, Any]:
                    schema = fn_to_input_schema(ci.fn)
                    schema["option_placement"] = ci.option_placement
                    return schema

                sub = subparsers.add_parser(
                    cmd_name,
                    help=cmd_info.help,
                    description=cmd_info.help,
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                    add_help=False,
                )
                self._inject_global_flags(
                    sub,
                    schema_fn=_cmd_schema_fn,
                )
                self._add_fn_params(sub, cmd_info.fn)
                if cmd_info.mutating:
                    sub.add_argument(
                        "--dry-run",
                        action="store_true",
                        default=False,
                        help="Print without writing",
                    )
                    sub.add_argument(
                        "--idempotency-key",
                        default=None,
                        metavar="KEY",
                        dest="idempotency_key",
                        help=(
                            "Caller-supplied key; duplicate calls with the same key"
                            " return the original result (REQ-C-007)"
                        ),
                    )
                sub.set_defaults(
                    _cmd_info=cmd_info,
                    _callbacks=my_callbacks,
                    _command_path=[cmd_name],
                )

            for sub_name, sub_app in self._sub_apps.items():
                sub = subparsers.add_parser(
                    sub_name,
                    help=sub_app.help or "",
                    add_help=False,
                )
                sub_app._mount_into(sub, callbacks=my_callbacks, path_prefix=[sub_name])

        return parser

    def _mount_into(
        self,
        parent: argparse.ArgumentParser,
        callbacks: list[Callable],
        path_prefix: list[str],
    ) -> None:
        """Mount this app's commands into an existing subparser slot."""
        self._inject_global_flags(parent, schema_fn=self.get_schema)
        my_callbacks = callbacks.copy()
        if self._callback_fn:
            my_callbacks.append(self._callback_fn)

        if self._commands or self._sub_apps:
            subparsers = parent.add_subparsers(dest="_command", metavar="COMMAND")
            subparsers.required = True
            for cmd_name, cmd_info in self._commands.items():

                def _sub_cmd_schema_fn(ci: CommandInfo = cmd_info) -> dict[str, Any]:
                    schema = fn_to_input_schema(ci.fn)
                    schema["option_placement"] = ci.option_placement
                    return schema

                sub = subparsers.add_parser(cmd_name, help=cmd_info.help, add_help=False)
                self._inject_global_flags(
                    sub,
                    schema_fn=_sub_cmd_schema_fn,
                )
                self._add_fn_params(sub, cmd_info.fn)
                if cmd_info.mutating:
                    sub.add_argument(
                        "--dry-run",
                        action="store_true",
                        default=False,
                        help="Print without writing",
                    )
                    sub.add_argument(
                        "--idempotency-key",
                        default=None,
                        metavar="KEY",
                        dest="idempotency_key",
                        help=(
                            "Caller-supplied key; duplicate calls with the same key"
                            " return the original result (REQ-C-007)"
                        ),
                    )
                sub.set_defaults(
                    _cmd_info=cmd_info,
                    _callbacks=my_callbacks,
                    _command_path=[*path_prefix, cmd_name],
                )

            for sub_name, sub_app in self._sub_apps.items():
                sub = subparsers.add_parser(
                    sub_name,
                    help=sub_app.help or "",
                    add_help=False,
                )
                sub_app._mount_into(sub, callbacks=my_callbacks, path_prefix=[*path_prefix, sub_name])

    def _inject_global_flags(
        self,
        parser: argparse.ArgumentParser,
        schema_fn: Callable[[], dict[str, Any]],
    ) -> None:
        """Add the standard agentyper global flags to a parser."""
        # -h / --help (REQ-F-048: routes to stderr in non-TTY)
        parser.add_argument(
            "-h",
            "--help",
            action=_HelpAction,
            help="Show this help message and exit",
        )

        # --schema (eager)
        parser.add_argument(
            "--schema",
            action=_SchemaPrintAction,
            schema_fn=schema_fn,
            help="Print command JSON Schema and exit",
        )

        # --format  (REQ-F-003: auto-JSON in CI environments)
        default_format = "table" if (sys.stdout.isatty() and not _is_ci()) else "json"
        env_format = os.getenv("AGENTYPER_FORMAT", default_format)
        parser.add_argument(
            "--format",
            choices=["table", "json", "csv"],
            default=env_format,
            metavar="FORMAT",
            help="Output format: table (default in TTY), json, csv",
        )

        # --yes / --no
        parser.add_argument(
            "--yes", "-y", action="store_true", default=False, help="Auto-confirm all prompts"
        )
        parser.add_argument(
            "--no", action="store_true", default=False, help="Auto-deny all confirm() calls"
        )

        # --answers
        parser.add_argument(
            "--answers",
            "-a",
            default=None,
            metavar="JSON",
            help="JSON string, file path, or '-' for STDIN with interactive answers",
        )

        # --timeout (REQ-F-011: per-invocation override)
        parser.add_argument(
            "--timeout",
            type=int,
            default=0,
            dest="_timeout_ms",
            metavar="MS",
            help="Wall-clock timeout in milliseconds (0 = use framework default)",
        )

        # -v / -vv verbosity
        parser.add_argument(
            "-v", action="count", default=0, dest="verbose", help="Verbose (-v INFO, -vv DEBUG)"
        )

        # --version
        if self.version:
            parser.add_argument(
                "--version",
                action="version",
                version=f"{self.name or 'agentyper'} {self.version}",
            )

    def _add_fn_params(
        self,
        parser: argparse.ArgumentParser,
        fn: Callable,
    ) -> None:
        """Introspect a function signature and add its parameters to the parser."""
        hints = get_type_hints(fn)

        sig = inspect.signature(fn)
        _skip = _GLOBAL_PARAMS | {
            "dry_run",
            "ctx",
            "_ctx",
            "context",
            "idempotency_key",
            "_timeout_ms",
        }

        for param_name, param in sig.parameters.items():
            if param_name in _skip:
                continue

            annotation = hints.get(param_name, str)
            default = param.default

            if isinstance(default, OptionInfo):
                self._add_option(parser, param_name, annotation, default)
            elif isinstance(default, ArgumentInfo):
                self._add_argument(parser, param_name, annotation, default)
            elif default is inspect.Parameter.empty or default is ...:
                # Required positional
                parser.add_argument(param_name, type=_make_type_fn(annotation))
            else:
                # Plain default → optional flag
                flag_name = f"--{param_name.replace('_', '-')}"
                if annotation is bool:
                    parser.add_argument(flag_name, action="store_true", default=default)
                else:
                    parser.add_argument(flag_name, type=_make_type_fn(annotation), default=default)

    def _add_option(
        self,
        parser: argparse.ArgumentParser,
        param_name: str,
        annotation: Any,
        info: OptionInfo,
    ) -> None:
        """Add an OptionInfo-described parameter to the parser."""
        # Determine flag names
        if info.param_decls:
            decls = list(info.param_decls)
        else:
            decls = [f"--{param_name.replace('_', '-')}"]

        has_default = info.has_default
        default_val = info.default
        if info.envvar:
            env_val = _resolve_envvar(info.envvar)
            if env_val is not None:
                has_default = True
                default_val = env_val

        kwargs: dict[str, Any] = {"help": info.help, "dest": param_name}

        if annotation is bool or info.is_flag:
            kwargs["action"] = "store_true"
            if isinstance(default_val, str):
                kwargs["default"] = default_val.lower() not in ("0", "false", "no", "n")
            else:
                kwargs["default"] = default_val if has_default else False
        else:
            kwargs["type"] = _make_type_fn(annotation)
            if has_default and default_val not in (..., None):
                kwargs["default"] = default_val
                if info.show_default:
                    kwargs["help"] += f" [default: {default_val}]"
            elif not has_default or default_val is ...:
                kwargs["required"] = True
            if info.metavar:
                kwargs["metavar"] = info.metavar

        if info.hidden:
            kwargs["help"] = argparse.SUPPRESS

        parser.add_argument(*decls, **kwargs)

    def _add_argument(
        self,
        parser: argparse.ArgumentParser,
        param_name: str,
        annotation: Any,
        info: ArgumentInfo,
    ) -> None:
        """Add an ArgumentInfo-described positional to the parser."""
        has_default = info.has_default
        default_val = info.default
        if info.envvar:
            env_val = _resolve_envvar(info.envvar)
            if env_val is not None:
                has_default = True
                default_val = env_val

        kwargs: dict[str, Any] = {"help": info.help}

        inner = _list_inner_type(annotation)
        if inner is not None:
            # Variadic positional: list[T] → nargs="+" (required) or nargs="*" (optional)
            required = not has_default or default_val is ...
            kwargs["nargs"] = "+" if required else "*"
            kwargs["type"] = _make_type_fn(inner)
            if has_default and default_val not in (..., None):
                kwargs["default"] = default_val
        else:
            if has_default and default_val is not ...:
                kwargs["nargs"] = "?"
                kwargs["default"] = default_val
            kwargs["type"] = _make_type_fn(annotation)

        if info.metavar:
            kwargs["metavar"] = info.metavar
        parser.add_argument(param_name, **kwargs)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def __call__(self, args: list[str] | None = None) -> None:
        """Parse arguments and dispatch to the appropriate command."""
        raw_args = list(args) if args is not None else sys.argv[1:]
        parser = self._build_parser()
        ns = parser.parse_args(raw_args)

        configure_logging(getattr(ns, "verbose", 0))

        # Set up interactive session
        session = InteractiveSession.from_parsed(
            auto_yes=getattr(ns, "yes", False),
            auto_no=getattr(ns, "no", False),
            answers_raw=getattr(ns, "answers", None),
        )
        set_session(session)

        # Resolve format
        format_ = getattr(ns, "format", "table")
        set_format(format_)
        set_start_time()
        clear_warnings()
        _bootstrap(format_)

        cmd_info: CommandInfo | None = getattr(ns, "_cmd_info", None)
        command_path: list[str] | None = getattr(ns, "_command_path", None)
        callbacks: list[Callable] = getattr(ns, "_callbacks", [])

        if not cmd_info and not callbacks and self._callback_fn:
            callbacks = [self._callback_fn]

        ctx = Context(format_=format_)

        for cb in callbacks:
            _call_fn(cb, ns, format_, ctx=ctx)

        if cmd_info is None:
            if not self.invoke_without_command and not callbacks:
                parser.print_help()
            return

        _validate_strict_option_placement(
            raw_args,
            cmd_info=cmd_info,
            command_path=command_path,
            has_version=self.version is not None,
            parser=parser,
        )

        # REQ-F-011: resolve effective timeout (env override > per-command > app default)
        effective_timeout = (
            int(os.getenv("TOOL_TIMEOUT_MS", "0"))
            or getattr(ns, "_timeout_ms", 0)
            or (cmd_info.timeout_ms if cmd_info.timeout_ms is not None else self.default_timeout_ms)
        )
        set_timeout_ms(effective_timeout)
        _install_timeout(effective_timeout, format_)

        # REQ-C-007: idempotency key deduplication for mutating/destructive commands
        ikey: str | None = getattr(ns, "idempotency_key", None) if cmd_info.mutating else None
        if ikey is not None:
            cache_key = f"{cmd_info.name}:{ikey}"
            if cache_key in _idempotency_cache:
                cached = _idempotency_cache[cache_key]
                noop = {**cached, "effect": "noop"}
                _cancel_timeout()
                render_output(noop, format_=format_)
                return

        result = _invoke_fn(cmd_info.fn, ns, format_, ctx=ctx)
        _cancel_timeout()
        if result is not None:
            if ikey is not None:
                # Cache the raw dict representation
                if isinstance(result, dict):
                    _idempotency_cache[f"{cmd_info.name}:{ikey}"] = result
                elif hasattr(result, "model_dump"):
                    _idempotency_cache[f"{cmd_info.name}:{ikey}"] = result.model_dump()
            render_output(result, format_=format_)


# ---------------------------------------------------------------------------
# Simple single-function runner
# ---------------------------------------------------------------------------


def run(
    fn: Callable,
    args: list[str] | None = None,
    *,
    prog: str | None = None,
    version: str | None = None,
) -> None:
    """
    Run a single function as a complete CLI application.

    Mirrors ``typer.run()``, but adds ``--schema``, ``--format``,
    ``--yes``, ``--no``, ``--answers``, ``-v``, ``-vv`` automatically.

    Example::

        def main(name: str, count: int = 1):
            \"\"\"Greet a user.\"\"\"
            for _ in range(count):
                echo(f"Hello {name}!")

        agentyper.run(main)

    Args:
        fn:      The function to expose as a CLI.
        args:    Override ``sys.argv[1:]`` (useful for testing).
        prog:    Override the program name (defaults to ``fn.__name__``).
        version: Version string for ``--version`` flag.
    """
    app = Agentyper(name=prog or fn.__name__, version=version)

    # Build a flat parser (no subcommands)
    parser = _Parser(
        prog=prog or fn.__name__,
        description=inspect.cleandoc(fn.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    app._inject_global_flags(parser, schema_fn=lambda: fn_to_input_schema(fn))
    app._add_fn_params(parser, fn)

    ns = parser.parse_args(args)
    configure_logging(getattr(ns, "verbose", 0))

    session = InteractiveSession.from_parsed(
        auto_yes=getattr(ns, "yes", False),
        auto_no=getattr(ns, "no", False),
        answers_raw=getattr(ns, "answers", None),
    )
    set_session(session)

    format_ = getattr(ns, "format", "table")
    set_format(format_)
    set_start_time()
    clear_warnings()
    _bootstrap(format_)

    ctx = Context(format_=format_)
    result = _invoke_fn(fn, ns, format_, ctx=ctx)
    if result is not None:
        render_output(result, format_=format_)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bootstrap(format_: str) -> None:
    """
    Apply framework-level environment side-effects at dispatch time.

    REQ-F-008: NO_COLOR / CI detection — propagate to child processes.
    REQ-F-046: Pager suppression — child CLIs must not open a pager.
    REQ-F-053: Stdout unbuffering — agents need line-granular output.
    REQ-F-055: $EDITOR/$VISUAL no-op in non-TTY — block interactive editor spawns.
    REQ-F-056: Terminal width suppression in JSON mode.
    """
    # REQ-F-053: unbuffer stdout for line-granular streaming
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    if not sys.stdout.isatty():
        with contextlib.suppress(AttributeError):
            sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    # REQ-F-046: suppress pagers spawned by child processes
    os.environ["PAGER"] = "cat"
    os.environ["GIT_PAGER"] = "cat"
    os.environ["LESS"] = "-F -X -R"
    os.environ.setdefault("MORE", "")
    os.environ["MANPAGER"] = "cat"

    # REQ-F-055: no-op editor in non-TTY (prevents blocking on $EDITOR)
    if not sys.stdin.isatty():
        os.environ["EDITOR"] = "true"
        os.environ["VISUAL"] = "true"

    # REQ-F-008: propagate NO_COLOR when CI or explicitly requested
    if os.getenv("NO_COLOR") is not None or os.getenv("TERM") == "dumb" or _is_ci():
        os.environ["NO_COLOR"] = "1"

    # REQ-F-056: suppress terminal width so child processes don't wrap JSON output
    if format_ == "json":
        os.environ["COLUMNS"] = "0"
    else:
        # Don't leave a stale COLUMNS=0 from a previous JSON invocation
        os.environ.pop("COLUMNS", None)

    # REQ-F-014: SIGPIPE — exit cleanly when consumer closes the pipe (BrokenPipeError → exit 0)
    if hasattr(signal, "SIGPIPE"):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    # REQ-F-013: SIGTERM — emit partial JSON cancellation response and exit 143
    _fired: list[bool] = [False]  # mutable ref for re-entrancy guard

    def _sigterm_handler(signum: int, frame: Any) -> None:
        if _fired[0]:
            return
        _fired[0] = True
        if format_ == "json" or not sys.stdout.isatty():
            try:
                sys.stdout.write(
                    json.dumps(
                        {
                            "ok": False,
                            "partial": True,
                            "data": None,
                            "error": {
                                "code": "CANCELLED",
                                "message": "Command cancelled by SIGTERM",
                            },
                            "warnings": [],
                            "meta": {},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
            except Exception:
                pass
        os._exit(143)

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, _sigterm_handler)


def _install_timeout(timeout_ms: int, format_: str) -> None:
    """Install SIGALRM-based wall-clock timeout (Unix only). No-op on Windows/non-main-thread."""
    if timeout_ms <= 0 or not hasattr(signal, "SIGALRM"):
        return
    import time as _time  # noqa: PLC0415

    start = _time.monotonic()
    timeout_sec = max(1, (timeout_ms + 999) // 1000)

    _timed_out: list[bool] = [False]

    def _handler(signum: int, frame: Any) -> None:
        if _timed_out[0]:
            return
        _timed_out[0] = True
        elapsed = int((_time.monotonic() - start) * 1000)
        if format_ == "json" or not sys.stdout.isatty():
            try:
                sys.stdout.write(
                    json.dumps(
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "TIMEOUT",
                                "message": f"Command exceeded timeout of {timeout_ms}ms",
                                "retryable": True,
                                "phase": "execution",
                            },
                            "warnings": [],
                            "meta": {"duration_ms": elapsed, "timeout_ms": timeout_ms},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
            except Exception:
                pass
        os._exit(int(ExitCode.TIMEOUT))

    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout_sec)
    except (ValueError, OSError):
        pass


def _cancel_timeout() -> None:
    """Cancel any active SIGALRM timeout."""
    if hasattr(signal, "SIGALRM"):
        with contextlib.suppress(ValueError, OSError):
            signal.alarm(0)


def _resolve_envvar(envvar: str | list[str] | None) -> Any:
    """Return the value from environment variable if present, else None."""
    if not envvar:
        return None
    if isinstance(envvar, str):
        envvar = [envvar]
    for e in envvar:
        val = os.getenv(e)
        if val is not None:
            return val
    return None


def _load_json(raw: str, message: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(message) from e


def _list_inner_type(annotation: Any) -> Any | None:
    """Return the inner type if annotation is list[T] or List[T], else None."""
    origin = getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        args = [a for a in annotation.__args__ if a is not type(None)]
        if args:
            return _list_inner_type(args[0])
    if origin in (list, collections.abc.Sequence):
        type_args = getattr(annotation, "__args__", None)
        return type_args[0] if type_args else str
    return None


def _make_type_fn(annotation: Any) -> Callable:
    """Return a type coercion callable safe for argparse ``type=`` argument."""
    origin = getattr(annotation, "__origin__", None)
    # Optional[X] → unwrap to X
    if origin is typing.Union:
        args = [a for a in annotation.__args__ if a is not type(None)]
        if args:
            return _make_type_fn(args[0])

    # list[X] / List[X] → parse from JSON array string
    if origin in (list, collections.abc.Sequence):
        type_args = getattr(annotation, "__args__", None)
        item_type = type_args[0] if type_args else str
        coerce = _make_type_fn(item_type)

        def _parse_list(raw: str) -> list:
            data = _load_json(raw, f"Invalid JSON array: {raw}")
            if not isinstance(data, list):
                raise argparse.ArgumentTypeError(
                    f"Expected a JSON array, got: {type(data).__name__}"
                )
            return [coerce(item) if isinstance(item, str) else item for item in data]

        return _parse_list

    if annotation is Path:
        return Path
    if annotation is ResourceId:
        # Validates against hallucination patterns before returning (REQ-F-044/045)
        def _validate_resource_id(value: str, _ann: type = annotation) -> str:
            return check_hallucination_patterns(value, "resource_id")

        return _validate_resource_id
    if annotation in (str, int, float):
        return annotation
    if annotation is bool:
        return lambda v: v.lower() not in ("0", "false", "no", "n")

    # Pydantic models → parse from JSON string
    if hasattr(annotation, "model_validate"):

        def _parse_model(raw: str) -> Any:
            data = _load_json(raw, f"Invalid JSON for {annotation.__name__}: {raw}")
            try:
                return annotation.model_validate(data)
            except Exception as e:
                raise argparse.ArgumentTypeError(str(e)) from e

        return _parse_model

    return str


def _build_kwargs(
    fn: Callable,
    ns: argparse.Namespace,
    format_: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Extract function kwargs from the parsed namespace, skipping framework-injected params."""
    sig = inspect.signature(fn)
    _skip = _GLOBAL_PARAMS | {"_command", "_cmd_info", "idempotency_key", "_timeout_ms"}
    kwargs: dict[str, Any] = {}
    for pname, _param in sig.parameters.items():
        if pname in _skip:
            continue
        val = getattr(ns, pname, None)
        if val is not None or pname in vars(ns):
            kwargs[pname] = val
    if "format_" in sig.parameters:
        kwargs["format_"] = format_
    if ctx is not None:
        if "ctx" in sig.parameters:
            kwargs["ctx"] = ctx
        elif "context" in sig.parameters:
            kwargs["context"] = ctx
    return kwargs


def _invoke_fn(
    fn: Callable, ns: argparse.Namespace, format_: str, ctx: Context | None = None
) -> Any:
    """Call fn with extracted kwargs and return the raw result (no rendering)."""
    kwargs = _build_kwargs(fn, ns, format_, ctx)
    try:
        return fn(**kwargs)
    except _PydanticValidationError as exc:
        format_pydantic_error(exc, format_=format_)
        return None


def _call_fn(
    fn: Callable, ns: argparse.Namespace, format_: str, ctx: Context | None = None
) -> None:
    """Call fn, render result. Used for callbacks (which don't participate in idempotency)."""
    result = _invoke_fn(fn, ns, format_, ctx)
    if result is not None:
        render_output(result, format_=format_)


# ---------------------------------------------------------------------------
# Compatibility aliases
# ---------------------------------------------------------------------------


class Exit(SystemExit):
    """Mirroring typer.Exit for compatibility."""

    def __init__(self, code: int = EXIT_SUCCESS) -> None:
        super().__init__(code)


class Abort(KeyboardInterrupt):
    """Mirroring typer.Abort for compatibility."""


class BadParameter(ValueError):
    """Mirroring typer.BadParameter for compatibility."""

    def __init__(self, message: str, param_name: str | None = None) -> None:
        super().__init__(message)
        self.param_name = param_name


class Context:
    """Minimal context object for Typer compatibility."""

    def __init__(self, format_: str = "table") -> None:
        self.format_ = format_
        self.obj: dict[str, Any] = {}
