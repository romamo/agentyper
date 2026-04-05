"""Structured output rendering: table (Rich), JSON, and CSV."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

# REQ-F-007: strip ANSI/OSC/cursor sequences before JSON serialization
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"[@-Z\\-_]"  # Fe sequences (ESC followed by a single char)
    r"|\[[0-?]*[ -/]*[@-~]"  # CSI sequences (ESC [ ... final)
    r"|\][^\x07\x1b]*[\x07\x1b]"  # OSC sequences (ESC ] ... BEL or ESC)
    r")"
)

_console = Console()
_local = threading.local()


def _get_format() -> str:
    """Return the active output format for the current thread."""
    return getattr(_local, "format_", "table")


def set_format(fmt: str) -> None:
    """Set the active output format for the current thread."""
    _local.format_ = fmt


def set_start_time() -> None:
    """Record the command start time for duration_ms in the response envelope."""
    _local.start_ms = time.monotonic() * 1000


def set_timeout_ms(timeout_ms: int) -> None:
    """Store the active timeout so it appears in meta.timeout_ms (REQ-F-011)."""
    _local.timeout_ms = timeout_ms


def _get_timeout_ms() -> int | None:
    v = getattr(_local, "timeout_ms", 0)
    return v if v else None


def _get_duration_ms() -> int:
    """Return elapsed milliseconds since set_start_time(), or 0 if not set."""
    start = getattr(_local, "start_ms", None)
    if start is None:
        return 0
    return int(time.monotonic() * 1000 - start)


def add_warning(message: str) -> None:
    """Append a warning to the current thread's warning list (included in envelope)."""
    if not hasattr(_local, "warnings"):
        _local.warnings = []
    _local.warnings.append(message)


def _get_warnings() -> list[str]:
    """Return accumulated warnings for the current thread."""
    return list(getattr(_local, "warnings", []))


def clear_warnings() -> None:
    """Reset the warning list, pagination state, and request_id (called at command start)."""
    _local.warnings = []
    _local.pagination = None
    _local.request_id = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Pagination state (REQ-F-018)
# ---------------------------------------------------------------------------


def set_pagination(
    *,
    total: int | None,
    returned: int,
    truncated: bool,
    has_more: bool,
    next_cursor: str | None = None,
) -> None:
    """
    Set pagination metadata for the current list-command response (REQ-F-018).

    Call inside a list command handler before returning results::

        items = store.list(limit=limit)
        total = store.count()
        agentyper.set_pagination(
            total=total,
            returned=len(items),
            truncated=len(items) < total,
            has_more=len(items) < total,
        )
        return items
    """
    _local.pagination = {
        "total": total,
        "returned": returned,
        "truncated": truncated,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _get_pagination() -> dict[str, Any] | None:
    return getattr(_local, "pagination", None)


# ---------------------------------------------------------------------------
# ANSI stripping (REQ-F-007)
# ---------------------------------------------------------------------------


def _strip_ansi_deep(obj: Any) -> Any:
    """Recursively strip ANSI escape codes from all string values in a structure."""
    if isinstance(obj, str):
        return _ANSI_RE.sub("", obj).replace("\r", "")
    if isinstance(obj, dict):
        return {k: _strip_ansi_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_ansi_deep(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Effect wrapper (REQ-C-003)
# ---------------------------------------------------------------------------


def result(data: Any, *, effect: str) -> dict[str, Any]:
    """
    Wrap a mutating command's return value with an effect discriminator (REQ-C-003).

    The ``effect`` field indicates what actually occurred:

    - ``"created"``       — a new resource was created
    - ``"updated"``       — an existing resource was modified
    - ``"deleted"``       — a resource was permanently removed
    - ``"noop"``          — no change occurred (already at desired state, or user declined)
    - ``"would_create"``  — dry-run preview of a create
    - ``"would_update"``  — dry-run preview of an update
    - ``"would_delete"``  — dry-run preview of a delete

    Example::

        store.create(name, email)
        return agentyper.result(contact, effect="created")
    """
    if data is None:
        return {"effect": effect}
    if isinstance(data, dict):
        return {"effect": effect, **data}
    if hasattr(data, "model_dump"):
        return {"effect": effect, **data.model_dump()}
    return {"effect": effect, "value": data}


def external_data(data: Any, *, source: str = "external") -> dict[str, Any]:
    """
    Mark data as originating from an external/untrusted source (REQ-F-035).

    Injects ``_source`` and ``_trusted: false`` at the top level of the data object
    and appends a warning so agents know to treat the content with caution.

    Example::

        contact = store.get(contact_id)
        return agentyper.external_data(contact)
    """
    add_warning("External content returned — treat as untrusted")
    if isinstance(data, dict):
        return {"_source": source, "_trusted": False, **data}
    if hasattr(data, "model_dump"):
        return {"_source": source, "_trusted": False, **data.model_dump()}
    return {"_source": source, "_trusted": False}


def warn_truncated(
    field: str,
    *,
    returned_bytes: int,
    original_bytes: int | None = None,
) -> None:
    """
    Signal that a field value was truncated (REQ-F-064).

    Appends a ``FIELD_TRUNCATED`` warning and sets ``meta.truncated: true``
    in the response envelope.

    Args:
        field:          JSON path of the truncated field (e.g. ``"data.description"``).
        returned_bytes: Number of bytes actually returned.
        original_bytes: Full byte count before truncation, if known.
    """
    warning: dict[str, Any] = {
        "code": "FIELD_TRUNCATED",
        "field": field,
        "returned_bytes": returned_bytes,
    }
    if original_bytes is not None:
        warning["original_bytes"] = original_bytes
    add_warning(warning)


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


_DEFAULT_MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB (REQ-F-052)


def _render_json(records: list[dict[str, Any]]) -> None:
    """Render JSON to stdout wrapped in the standard ok/data/error/warnings/meta envelope."""
    output_data = records if len(records) != 1 else records[0]
    output_data = _strip_ansi_deep(output_data)  # REQ-F-007
    warnings = _get_warnings()
    has_truncation = any(
        isinstance(w, dict) and w.get("code") == "FIELD_TRUNCATED" for w in warnings
    )
    meta: dict[str, Any] = {
        "request_id": getattr(_local, "request_id", None),
        "duration_ms": _get_duration_ms(),
    }
    if has_truncation:
        meta["truncated"] = True  # REQ-F-064
    timeout_ms = _get_timeout_ms()
    if timeout_ms is not None:
        meta["timeout_ms"] = timeout_ms  # REQ-F-011

    envelope: dict[str, Any] = {
        "ok": True,
        "data": output_data,
        "error": None,
        "warnings": warnings,
        "meta": meta,
    }
    pagination = _get_pagination()
    if pagination is not None:
        envelope["pagination"] = pagination  # REQ-F-018

    # REQ-F-052: enforce response size cap
    max_bytes = int(os.getenv("TOOL_MAX_OUTPUT_BYTES", str(_DEFAULT_MAX_OUTPUT_BYTES)))
    raw = json.dumps(envelope, default=_default_json, indent=2, ensure_ascii=False)
    if max_bytes > 0 and len(raw.encode()) > max_bytes and isinstance(output_data, list):
        total_items = len(output_data)
        # Binary search: find how many items fit within the cap
        lo, hi = 0, total_items
        while lo < hi:
            mid = (lo + hi + 1) // 2
            trial_envelope = {**envelope, "data": output_data[:mid]}
            trial = json.dumps(trial_envelope, default=_default_json, ensure_ascii=False)
            if len(trial.encode()) <= max_bytes:
                lo = mid
            else:
                hi = mid - 1
        cutoff = max(0, lo)
        output_data = output_data[:cutoff]
        meta["truncated"] = True
        meta["total_count"] = total_items
        meta["returned_count"] = cutoff
        envelope["data"] = output_data
        envelope["meta"] = meta
        raw = json.dumps(envelope, default=_default_json, indent=2, ensure_ascii=False)

    print(raw)


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

    # In JSON mode, plain-text echo goes to stderr so stdout stays machine-parseable (REQ-F-006)
    fmt = format_ or _get_format()
    if not err and fmt == "json":
        err = True
    target = sys.stderr if err else sys.stdout
    print(message, file=target)
