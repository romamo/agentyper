"""Option and Argument factory functions for agentyper commands."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

# ---------------------------------------------------------------------------
# Agent hallucination input patterns (REQ-F-045, REQ-F-044)
# ---------------------------------------------------------------------------

_HALLUCINATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\.\.[\\/]|\.\.%2[fF]|\.\.%5[cC]|%2[eE]%2[eE][\\/]", re.IGNORECASE),
        "path_traversal",
    ),
    (re.compile(r"%2[eEfF]|%5[cC]", re.IGNORECASE), "percent_encoded_separator"),
    (re.compile(r"[?&]"), "query_parameter"),
    (re.compile(r"#"), "fragment_identifier"),
    (re.compile(r"[;\n\r`$|<>()]"), "shell_metacharacter"),
]


def check_hallucination_patterns(value: str, arg_name: str) -> str:
    """
    Validate a resource ID / path string against known agent hallucination patterns.

    Raises SystemExit(ARG_ERROR) if a pattern matches.
    Returns the value unchanged if all checks pass.
    """
    # Import lazily to avoid circular imports
    from agentyper._internal._errors import ExitCode, exit_error  # noqa: PLC0415

    for pattern, name in _HALLUCINATION_PATTERNS:
        if pattern.search(value):
            exit_error(
                f"Argument '{arg_name}' contains a rejected hallucination pattern",
                code=ExitCode.ARG_ERROR,
                error_type="INVALID_AGENT_INPUT",
                field=arg_name,
                constraint=f"rejected_pattern: {name}",
                hint=(
                    f"Value '{value[:40]}' matched pattern '{name}'."
                    " Check for encoding or injection."
                ),
                format_="json",
            )
    return value


class ResourceId(str):
    """
    Type annotation for resource identifier arguments.

    The framework automatically validates against agent hallucination patterns
    (path traversal, percent-encoded separators, query strings, shell metacharacters)
    when this type is used as a parameter annotation. Use it wherever a command
    accepts an opaque ID from the caller::

        def get(contact_id: ResourceId = Argument(...)):
            ...
    """


_MISSING = object()


@dataclasses.dataclass(slots=True)
class OptionInfo:
    """Metadata attached to a parameter declared with Option()."""

    default: Any
    param_decls: tuple[str, ...]  # e.g. ("--limit", "-l")
    help: str
    show_default: bool
    hidden: bool
    is_flag: bool  # True for bool parameters with no value
    metavar: str | None
    envvar: str | list[str] | None

    # Sentinel to distinguish "no default" from None
    _missing: object = dataclasses.field(default=_MISSING, repr=False, compare=False)

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING

    # Tag so _app.py can detect this object
    _agentyper_option: bool = dataclasses.field(default=True, init=False, repr=False)


@dataclasses.dataclass(slots=True)
class ArgumentInfo:
    """Metadata attached to a positional parameter declared with Argument()."""

    default: Any
    help: str
    metavar: str | None
    envvar: str | list[str] | None

    _missing: object = dataclasses.field(default=_MISSING, repr=False, compare=False)

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING

    _agentyper_argument: bool = dataclasses.field(default=True, init=False, repr=False)


def Option(
    default: Any = _MISSING,
    *param_decls: str,
    help: str = "",
    show_default: bool = True,
    hidden: bool = False,
    is_flag: bool = False,
    metavar: str | None = None,
    envvar: str | list[str] | None = None,
) -> Any:
    """
    Declare a CLI option (flag), mirroring ``typer.Option()``.

    Usage::

        def cmd(limit: int = Option(10, "--limit", "-l", help="Max rows")):
            ...

    Args:
        default:      Default value. Pass ``...`` (Ellipsis) for required options.
        *param_decls: CLI names, e.g. ``"--limit"``, ``"-l"``.
                      If omitted, agentyper derives the name from the parameter name.
        help:         Description shown in ``--help`` output.
        show_default: Whether to show the default in ``--help``.
        hidden:       Exclude this option from ``--help`` and ``--schema`` output.
        is_flag:      True for boolean flags (``--verbose`` style, no value).
        metavar:      Override the metavar shown in ``--help`` (e.g. ``"PATH"``).

    Returns:
        An :class:`OptionInfo` sentinel recognised by :class:`~agentyper.Agentyper`.
    """
    real_default = _MISSING if default is _MISSING else default
    return OptionInfo(
        default=real_default,
        param_decls=param_decls,
        help=help,
        show_default=show_default,
        hidden=hidden,
        is_flag=is_flag,
        metavar=metavar,
        envvar=envvar,
        _missing=_MISSING,
    )


def Argument(
    default: Any = _MISSING,
    *,
    help: str = "",
    metavar: str | None = None,
    envvar: str | list[str] | None = None,
) -> Any:
    """
    Declare a CLI positional argument, mirroring ``typer.Argument()``.

    Usage::

        def cmd(ticker: str = Argument(..., help="Ticker symbol")):
            ...

    Args:
        default:  Default value. Pass ``...`` (Ellipsis) for required arguments.
        help:     Description shown in ``--help`` output.
        metavar:  Override the metavar in ``--help`` (e.g. ``"TICKER"``).

    Returns:
        An :class:`ArgumentInfo` sentinel recognised by :class:`~agentyper.Agentyper`.
    """
    real_default = _MISSING if default is _MISSING else default
    return ArgumentInfo(
        default=real_default,
        help=help,
        metavar=metavar,
        envvar=envvar,
        _missing=_MISSING,
    )
