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
import dataclasses
import inspect
import json
import os
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import ValidationError as _PydanticValidationError

from agentyper._internal._errors import EXIT_SUCCESS, format_pydantic_error
from agentyper._internal._logging import configure_logging
from agentyper._internal._output import render_output, set_format
from agentyper._internal._params import ArgumentInfo, OptionInfo
from agentyper._internal._schema import build_app_schema, fn_to_input_schema
from agentyper._internal._session import InteractiveSession, set_session

# ---------------------------------------------------------------------------
# Internal bookkeeping
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CommandInfo:
    name: str
    fn: Callable
    help: str
    mutating: bool


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
    ) -> None:
        self.name = name
        self.version = version
        self.help = help
        self.invoke_without_command = invoke_without_command
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
    ) -> Callable:
        """
        Register a function as a CLI subcommand.

        Args:
            name:    Override the command name (defaults to function name with _ → -).
            help:    Override the help/description (defaults to function docstring).
            mutating: Mark as a write/mutation command (auto-adds ``--dry-run``).
        """

        def decorator(fn: Callable) -> Callable:
            cmd_name = name or fn.__name__.rstrip("_").replace("_", "-")
            cmd_help = help or inspect.cleandoc(fn.__doc__ or "")
            self._commands[cmd_name] = CommandInfo(
                name=cmd_name, fn=fn, help=cmd_help, mutating=mutating
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
        parser = argparse.ArgumentParser(
            prog=self.name,
            description=self.help,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        self._inject_global_flags(parser, schema_fn=self.get_schema)

        my_callbacks = callbacks.copy()
        if self._callback_fn:
            my_callbacks.append(self._callback_fn)

        if self._commands or self._sub_apps:
            subparsers = parser.add_subparsers(dest="_command", metavar="COMMAND")
            subparsers.required = not self.invoke_without_command

            for cmd_name, cmd_info in self._commands.items():

                def _cmd_schema_fn(ci: CommandInfo = cmd_info) -> dict[str, Any]:
                    return fn_to_input_schema(ci.fn)

                sub = subparsers.add_parser(
                    cmd_name,
                    help=cmd_info.help,
                    description=cmd_info.help,
                    formatter_class=argparse.RawDescriptionHelpFormatter,
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
                sub.set_defaults(_cmd_info=cmd_info, _callbacks=my_callbacks)

            for sub_name, sub_app in self._sub_apps.items():
                sub = subparsers.add_parser(
                    sub_name,
                    help=sub_app.help or "",
                    add_help=True,
                )
                sub_app._mount_into(sub, callbacks=my_callbacks)

        return parser

    def _mount_into(self, parent: argparse.ArgumentParser, callbacks: list[Callable]) -> None:
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
                    return fn_to_input_schema(ci.fn)

                sub = subparsers.add_parser(cmd_name, help=cmd_info.help)
                self._inject_global_flags(
                    sub,
                    schema_fn=_sub_cmd_schema_fn,
                )
                self._add_fn_params(sub, cmd_info.fn)
                sub.set_defaults(_cmd_info=cmd_info, _callbacks=my_callbacks)

    def _inject_global_flags(
        self,
        parser: argparse.ArgumentParser,
        schema_fn: Callable[[], dict[str, Any]],
    ) -> None:
        """Add the standard agentyper global flags to a parser."""
        # --schema (eager)
        parser.add_argument(
            "--schema",
            action=_SchemaPrintAction,
            schema_fn=schema_fn,
            help="Print command JSON Schema and exit",
        )

        # --format
        default_format = "table" if sys.stdout.isatty() else "json"
        env_format = os.getenv("AGENTER_FORMAT", default_format)
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
        _skip = {  # fmt: off
            "format_",
            "schema",
            "yes",
            "no",
            "answers",
            "verbose",
            "version",
            "ctx",
            "_ctx",
            "context",
        }  # fmt: on

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
            elif not has_default:
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
        parser = self._build_parser()
        ns = parser.parse_args(args)

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

        cmd_info: CommandInfo | None = getattr(ns, "_cmd_info", None)
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

        _call_fn(cmd_info.fn, ns, format_, ctx=ctx)


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
    app._commands["__root__"] = CommandInfo(
        name="__root__",
        fn=fn,
        help=inspect.cleandoc(fn.__doc__ or ""),
        mutating=False,
    )

    # Build a flat parser (no subcommands)
    parser = argparse.ArgumentParser(
        prog=prog or fn.__name__,
        description=inspect.cleandoc(fn.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    ctx = Context(format_=format_)
    _call_fn(fn, ns, format_, ctx=ctx)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


def _make_type_fn(annotation: Any) -> Callable:
    """Return a type coercion callable safe for argparse ``type=`` argument."""
    origin = getattr(annotation, "__origin__", None)
    # Optional[X] → unwrap to X
    if origin is typing.Union:
        args = [a for a in annotation.__args__ if a is not type(None)]
        if args:
            return _make_type_fn(args[0])

    if annotation is Path:
        return Path
    if annotation in (str, int, float):
        return annotation
    if annotation is bool:
        return lambda v: v.lower() not in ("0", "false", "no", "n")

    # Pydantic models → parse from JSON string
    if hasattr(annotation, "model_validate"):

        def _parse_model(raw: str) -> Any:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise argparse.ArgumentTypeError(
                    f"Invalid JSON for {annotation.__name__}: {raw}"
                ) from e
            try:
                return annotation.model_validate(data)
            except Exception as e:
                raise argparse.ArgumentTypeError(str(e)) from e

        return _parse_model

    return str


def _call_fn(
    fn: Callable, ns: argparse.Namespace, format_: str, ctx: Context | None = None
) -> None:
    # _ = get_type_hints(fn)

    sig = inspect.signature(fn)
    _skip = {  # noqa: E501
        "format_",
        "schema",
        "yes",
        "no",
        "answers",
        "verbose",
        "version",
        "_command",
        "_cmd_info",
    }
    kwargs: dict[str, Any] = {}

    for pname, _param in sig.parameters.items():
        if pname in _skip:
            continue
        if pname in ("format_",):
            kwargs[pname] = format_
            continue
        val = getattr(ns, pname, None)
        if val is not None or pname in vars(ns):
            kwargs[pname] = val

    # Inject output helper: pass format_ if function accepts it
    if "format_" in sig.parameters:
        kwargs["format_"] = format_

    if ctx is not None:
        if "ctx" in sig.parameters:
            kwargs["ctx"] = ctx
        elif "context" in sig.parameters:
            kwargs["context"] = ctx

    try:
        result = fn(**kwargs)
    except _PydanticValidationError as exc:
        format_pydantic_error(exc, format_=format_)

    # If function returned data, render it
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
