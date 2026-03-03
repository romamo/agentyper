"""
Exit codes and structured error output for agentyper.

Exit code taxonomy:
  EXIT_SUCCESS    = 0  — operation completed successfully
  EXIT_VALIDATION = 1  — bad input (agent should retry with corrected payload)
  EXIT_SYSTEM     = 2  — system/runtime error (agent should abort)
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, NoReturn

from rich.console import Console

if TYPE_CHECKING:
    pass  # pydantic imported lazily to avoid heavy startup cost

EXIT_SUCCESS: int = 0
EXIT_VALIDATION: int = 1
EXIT_SYSTEM: int = 2

_err_console = Console(stderr=True)


def exit_error(
    message: str,
    *,
    code: int = EXIT_VALIDATION,
    field: str | None = None,
    error_type: str = "Error",
    constraint: str | None = None,
    format_: str = "table",
) -> NoReturn:
    """
    Print a structured error and exit with the given code.

    In JSON mode (non-TTY or --format json), emits a JSON object to stderr.
    In table mode, emits a Rich-formatted error message to stderr.

    Args:
        message:    Human-readable error message.
        code:       Exit code (EXIT_VALIDATION or EXIT_SYSTEM).
        field:      The field that caused the error, if applicable.
        error_type: Short error category name.
        constraint: The violated constraint (e.g. "pattern: ^[A-Z]+$").
        format_:    Output format context ("json" emits JSON; else Rich text).
    """
    if format_ == "json" or not sys.stderr.isatty():
        payload: dict[str, object] = {
            "error": True,
            "error_type": error_type,
            "message": message,
            "exit_code": code,
        }
        if field is not None:
            payload["field"] = field
        if constraint is not None:
            payload["constraint"] = constraint
        print(json.dumps(payload), file=sys.stderr)
    else:
        parts = [f"[bold red]Error[/bold red]: {message}"]
        if field:
            parts.append(f"  Field: [yellow]{field}[/yellow]")
        if constraint:
            parts.append(f"  Constraint: [dim]{constraint}[/dim]")
        _err_console.print("\n".join(parts))

    sys.exit(code)


def format_pydantic_error(exc: object, format_: str = "table") -> NoReturn:
    """
    Serialize a Pydantic ValidationError to a structured error and exit 1.

    Args:
        exc:     A pydantic.ValidationError instance.
        format_: Output format context.
    """
    try:
        from pydantic import ValidationError as PydanticValidationError  # noqa: PLC0415

        if not isinstance(exc, PydanticValidationError):
            raise TypeError(f"Expected ValidationError, got {type(exc)}")

        errors = exc.errors(include_url=False)
    except ImportError:
        exit_error(str(exc), code=EXIT_VALIDATION, format_=format_)

    if format_ == "json" or not sys.stderr.isatty():
        payload = {
            "error": True,
            "error_type": "ValidationError",
            "exit_code": EXIT_VALIDATION,
            "errors": [
                {
                    "field": ".".join(str(loc) for loc in e["loc"]) if e["loc"] else None,
                    "message": e["msg"],
                    "type": e["type"],
                    **({} if "ctx" not in e else {"constraint": str(e["ctx"])}),
                }
                for e in errors
            ],
        }
        print(json.dumps(payload), file=sys.stderr)
        sys.exit(EXIT_VALIDATION)

    # Rich text output for humans
    _err_console.print("[bold red]Validation Error[/bold red]")
    for e in errors:
        field = ".".join(str(loc) for loc in e["loc"]) if e["loc"] else "(root)"
        _err_console.print(f"  [yellow]{field}[/yellow]: {e['msg']}")
    sys.exit(EXIT_VALIDATION)
