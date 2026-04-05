"""
Exit codes and structured error output for agentyper.

Implements REQ-F-001: Standard Exit Code Table (14 named codes).

Exit code table (from CLI Agent Spec):
  0  SUCCESS          — operation completed as intended
  1  GENERAL_ERROR    — unclassified failure; use specific code when available
  2  PARTIAL_FAILURE  — operation ran, partial writes occurred; not retryable
  3  ARG_ERROR        — input validation failed before any side effect; retryable
  4  PRECONDITION     — required precondition not met
  5  NOT_FOUND        — requested resource does not exist
  6  CONFLICT         — resource already exists or conflicts
  7  PERMISSION_DENIED — valid credentials, insufficient permissions
  8  AUTH_REQUIRED    — credentials missing or invalid
  9  PAYMENT_REQUIRED — payment required to proceed
  10 TIMEOUT          — operation exceeded its time limit; partial writes possible
  11 RATE_LIMITED     — server-side rate limit; retry after back-off
  12 UNAVAILABLE      — service temporarily unavailable
  13 REDIRECTED       — command renamed; use error.redirect.command

Reserved ranges (MUST NOT use):
  14–63   framework extensions
  64–78   POSIX sysexits (optional mapping)
  79–125  command-specific (declare via REQ-C-001)
  126–255 shell-reserved
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Literal, NoReturn

from rich.console import Console

if TYPE_CHECKING:
    pass  # pydantic imported lazily to avoid heavy startup cost


# ---------------------------------------------------------------------------
# Exit code enum — 14 named codes; MUST NOT use literal integers at call sites
# ---------------------------------------------------------------------------


class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL_ERROR = 1
    PARTIAL_FAILURE = 2
    ARG_ERROR = 3
    PRECONDITION = 4
    NOT_FOUND = 5
    CONFLICT = 6
    PERMISSION_DENIED = 7
    AUTH_REQUIRED = 8
    PAYMENT_REQUIRED = 9
    TIMEOUT = 10
    RATE_LIMITED = 11
    UNAVAILABLE = 12
    REDIRECTED = 13


# ---------------------------------------------------------------------------
# ExitCodeEntry — metadata for each code (retryable + side_effects semantics)
# ---------------------------------------------------------------------------

SideEffects = Literal["none", "partial", "complete", "unknown"]


@dataclass(frozen=True)
class ExitCodeEntry:
    name: str
    description: str
    retryable: bool
    side_effects: SideEffects


def validate_exit_code_entry(entry: ExitCodeEntry) -> None:
    """
    Enforce the spec invariant: retryable=True implies side_effects='none'.

    Call this at command registration time for command-specific ExitCodeEntry
    declarations. Do NOT apply to the framework's global EXIT_CODE_TABLE —
    that table encodes TIMEOUT's default behavior (retryable, partial) which
    is overridden per-command.
    """
    if entry.retryable and entry.side_effects != "none":
        raise ValueError(
            f"ExitCodeEntry '{entry.name}': retryable=True requires "
            f"side_effects='none', got '{entry.side_effects}'"
        )


# ---------------------------------------------------------------------------
# Framework-level exit code table (REQ-F-001 wire format)
# ---------------------------------------------------------------------------

EXIT_CODE_TABLE: dict[int, ExitCodeEntry] = {
    ExitCode.SUCCESS: ExitCodeEntry(
        "SUCCESS", "Operation completed as intended", False, "complete"
    ),
    ExitCode.GENERAL_ERROR: ExitCodeEntry(
        "GENERAL_ERROR",
        "Unclassified failure — use a specific code when one is available",
        False,
        "unknown",
    ),
    ExitCode.PARTIAL_FAILURE: ExitCodeEntry(
        "PARTIAL_FAILURE",
        "Operation ran but failed mid-way; partial writes occurred",
        False,
        "partial",
    ),
    ExitCode.ARG_ERROR: ExitCodeEntry(
        "ARG_ERROR",
        "Input validation failed before any side effect",
        True,
        "none",
    ),
    ExitCode.PRECONDITION: ExitCodeEntry(
        "PRECONDITION",
        "A required precondition was not met",
        False,
        "none",
    ),
    ExitCode.NOT_FOUND: ExitCodeEntry(
        "NOT_FOUND",
        "The requested resource was not found",
        False,
        "none",
    ),
    ExitCode.CONFLICT: ExitCodeEntry(
        "CONFLICT",
        "Resource already exists or conflicts with existing state",
        False,
        "none",
    ),
    ExitCode.PERMISSION_DENIED: ExitCodeEntry(
        "PERMISSION_DENIED",
        "Valid credentials but insufficient permissions — escalate or change approach",
        False,
        "none",
    ),
    ExitCode.AUTH_REQUIRED: ExitCodeEntry(
        "AUTH_REQUIRED",
        "Credentials missing or invalid; retryable after acquiring credentials",
        True,
        "none",
    ),
    ExitCode.PAYMENT_REQUIRED: ExitCodeEntry(
        "PAYMENT_REQUIRED",
        "Payment required; retryable after payment",
        True,
        "none",
    ),
    ExitCode.TIMEOUT: ExitCodeEntry(
        "TIMEOUT",
        "Operation exceeded its configured time limit; partial writes possible",
        True,
        "partial",  # default; commands may declare retryable=False if writes occurred
    ),
    ExitCode.RATE_LIMITED: ExitCodeEntry(
        "RATE_LIMITED",
        "Server-side rate limit reached; retry after back-off",
        True,
        "none",
    ),
    ExitCode.UNAVAILABLE: ExitCodeEntry(
        "UNAVAILABLE",
        "Service temporarily unavailable; apply exponential back-off",
        True,
        "none",
    ),
    ExitCode.REDIRECTED: ExitCodeEntry(
        "REDIRECTED",
        "Command was renamed; use error.redirect.command verbatim",
        True,
        "none",
    ),
}

# ---------------------------------------------------------------------------
# Backward-compatible aliases
# EXIT_VALIDATION → ARG_ERROR (3)  — validation failure, zero side effects
# EXIT_SYSTEM     → GENERAL_ERROR (1) — unclassified external failure
# ---------------------------------------------------------------------------

EXIT_SUCCESS: int = ExitCode.SUCCESS  # 0
EXIT_VALIDATION: int = ExitCode.ARG_ERROR  # 3  (was 1 — see CHANGELOG)
EXIT_SYSTEM: int = ExitCode.GENERAL_ERROR  # 1  (was 2 — see CHANGELOG)

_err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Error output
# ---------------------------------------------------------------------------


def exit_error(
    message: str,
    *,
    code: int = EXIT_VALIDATION,
    field: str | None = None,
    error_type: str = "Error",
    constraint: str | None = None,
    hint: str | None = None,
    format_: str = "table",
) -> NoReturn:
    """
    Print a structured error and exit with the given code.

    In JSON mode (non-TTY or --format json), emits a JSON object to stderr.
    In table mode, emits a Rich-formatted error message to stderr.

    Args:
        message:    Human-readable error message.
        code:       Exit code — use ExitCode constants, not literal integers.
        field:      The field that caused the error, if applicable.
        error_type: Short error category name.
        constraint: The violated constraint (e.g. "pattern: ^[A-Z]+$").
        hint:       Actionable suggestion for the caller (included in JSON errors).
        format_:    Output format context ("json" emits JSON; else Rich text).
    """
    if format_ == "json" or not sys.stderr.isatty():
        payload: dict[str, object] = {
            "error": True,
            "error_type": error_type,
            "message": message,
            "exit_code": int(code),
        }
        if int(code) == ExitCode.ARG_ERROR:
            payload["phase"] = "validation"
        if field is not None:
            payload["field"] = field
        if constraint is not None:
            payload["constraint"] = constraint
        if hint is not None:
            payload["hint"] = hint
        print(json.dumps(payload), file=sys.stderr)
    else:
        parts = [f"[bold red]Error[/bold red]: {message}"]
        if field:
            parts.append(f"  Field: [yellow]{field}[/yellow]")
        if constraint:
            parts.append(f"  Constraint: [dim]{constraint}[/dim]")
        if hint:
            parts.append(f"  Hint: [cyan]{hint}[/cyan]")
        _err_console.print("\n".join(parts))

    sys.exit(int(code))


def format_pydantic_error(exc: object, format_: str = "table") -> NoReturn:
    """
    Serialize a Pydantic ValidationError to a structured error and exit ARG_ERROR (3).

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
        exit_error(str(exc), code=ExitCode.ARG_ERROR, format_=format_)

    _code = int(ExitCode.ARG_ERROR)
    if format_ == "json" or not sys.stderr.isatty():
        payload = {
            "error": True,
            "error_type": "ValidationError",
            "exit_code": _code,
            "phase": "validation",
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
        sys.exit(_code)

    # Rich text output for humans
    _err_console.print("[bold red]Validation Error[/bold red]")
    for e in errors:
        field = ".".join(str(loc) for loc in e["loc"]) if e["loc"] else "(root)"
        _err_console.print(f"  [yellow]{field}[/yellow]: {e['msg']}")
    sys.exit(_code)
