"""Structured output rendering: table (Rich), JSON, and CSV."""

from __future__ import annotations

import csv
import io
import json
import sys
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

_console = Console()
_local = threading.local()


def _get_format() -> str:
    """Return the active output format for the current thread."""
    return getattr(_local, "format_", "table")


def set_format(fmt: str) -> None:
    """Set the active output format for the current thread."""
    _local.format_ = fmt


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _default_json(obj: Any) -> Any:
    """Custom JSON encoder for types not handled by stdlib json."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    # Pydantic models
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _to_dict(record: Any) -> dict[str, Any]:
    """Coerce a record to a plain dict."""
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if hasattr(record, "__dict__"):
        return record.__dict__
    raise TypeError(f"Cannot convert {type(record)} to dict")


def _normalise(data: Any) -> list[dict[str, Any]]:
    """Return a consistent list[dict] regardless of input shape."""
    if isinstance(data, dict):
        return [data]
    if hasattr(data, "model_dump"):
        return [data.model_dump()]
    try:
        records = list(data)
    except TypeError:
        records = [data]
    return [_to_dict(r) for r in records]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_table(records: list[dict[str, Any]], title: str) -> None:
    """Render a Rich table to stdout."""
    if not records:
        _console.print("[dim](no results)[/dim]")
        return

    table = Table(title=title or None, show_header=True, header_style="bold cyan")
    for col in records[0]:
        table.add_column(col)
    for row in records:
        table.add_row(*[str(v) if v is not None else "" for v in row.values()])
    _console.print(table)


def _render_json(records: list[dict[str, Any]]) -> None:
    """Render JSON to stdout."""
    print(json.dumps(records if len(records) != 1 else records[0], default=_default_json, indent=2))


def _render_csv(records: list[dict[str, Any]]) -> None:
    """Render CSV to stdout."""
    if not records:
        return
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(
        {k: (str(v) if not isinstance(v, str | None.__class__) else v or "") for k, v in r.items()}
        for r in records
    )
    sys.stdout.write(buf.getvalue())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_output(
    data: Any,
    *,
    format_: str = "table",
    title: str = "",
) -> None:
    """
    Render structured data according to the requested format.

    Args:
        data:    A single dict, a Pydantic model, or an iterable of either.
        format_: ``"table"`` (default), ``"json"``, or ``"csv"``.
        title:   Optional table title (only shown in table mode).
    """
    records = _normalise(data)

    if format_ == "json":
        _render_json(records)
    elif format_ == "csv":
        _render_csv(records)
    else:
        _render_table(records, title)


def output(data: Any, *, format_: str | None = None, title: str = "") -> None:
    """
    Render structured data using the current format context.

    Intended for use inside command handlers. The format is taken from
    the thread-local set by the framework when the command is dispatched
    (i.e. the ``--format`` flag), unless overridden explicitly.

    Args:
        data:    A single dict, Pydantic model, or iterable of either.
        format_: Override the format for this call.
        title:   Optional table title (table mode only).
    """
    fmt = format_ or _get_format()
    render_output(data, format_=fmt, title=title)


def echo(message: Any = "", *, err: bool = False, format_: str | None = None) -> None:
    """
    Print a message or structured data.

    If ``message`` is a ``list`` or ``dict`` (or Pydantic model), it is routed
    through :func:`render_output` using the current format.

    Otherwise behaves like ``typer.echo()``.

    Args:
        message: String, list, dict, or Pydantic model.
        err:     If True, print to stderr.
        format_: Output format override (defaults to thread-local format).
    """
    if isinstance(message, (list, dict)) or hasattr(message, "model_dump"):
        fmt = format_ or _get_format()
        render_output(message, format_=fmt)
        return

    target = sys.stderr if err else sys.stdout
    print(message, file=target)
