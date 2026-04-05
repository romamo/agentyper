"""
contacts — thin agentyper CLI over ContactStore.

Run:  python examples/contacts/app.py --schema
      python examples/contacts/app.py list --format json
      python examples/contacts/app.py create --name "Dave" --email dave@example.com --yes

Every command gains for free — zero code required:
  --schema   · --format json/csv/table  · --yes/--no  · --answers
  --dry-run  · -v/-vv  · exit 0/1/2  · structured JSON errors to stderr

Business logic → store.py    Domain models → models.py    Agent usage → docs/for-agents.md
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).parent))

from models import Contact, ContactSummary
from store import ContactNotFound, ContactStore, DuplicateEmail, InvalidField

import agentyper
from agentyper import Argument, Option, ResourceId

log = logging.getLogger(__name__)
store = ContactStore()

app = agentyper.Agentyper(
    name="contacts",
    version="1.0.0",
    help="Manage contacts. Agent-friendly CLI built with agentyper.",
)


def _handle(exc: ContactNotFound | DuplicateEmail | InvalidField) -> None:
    """Translate domain exceptions to structured agentyper errors."""
    if isinstance(exc, ContactNotFound):
        agentyper.exit_error(
            str(exc),
            code=agentyper.ExitCode.NOT_FOUND,
            field="id",
            error_type="NotFound",
            hint="Use 'contacts list' to get valid contact IDs.",
        )
    elif isinstance(exc, DuplicateEmail):
        agentyper.exit_error(
            str(exc), code=agentyper.ExitCode.CONFLICT, field="email", error_type="Conflict"
        )
    elif isinstance(exc, InvalidField):
        agentyper.exit_error(
            str(exc), code=agentyper.ExitCode.ARG_ERROR, field=exc.field, constraint=exc.constraint
        )


def _get_or_exit(contact_id: str) -> Contact:
    try:
        return store.get(contact_id)
    except ContactNotFound as e:
        _handle(e)


@app.command(name="list")
def list_contacts(
    status: Literal["active", "archived", "all"] = Option(
        "active", help="Filter by status. 'all' includes archived."
    ),
    limit: int = Option(20, help="Maximum results (1–100)."),
) -> list[ContactSummary]:
    """List contacts as a compact summary. Use `get <ID>` for full details."""
    if not 1 <= limit <= 100:
        agentyper.exit_error(
            f"limit must be 1–100, got {limit}",
            field="limit",
            constraint="minimum: 1, maximum: 100",
            code=agentyper.ExitCode.ARG_ERROR,
        )
    log.debug("list status=%s limit=%s", status, limit)
    items = store.list(status=status, limit=limit)
    total = store.count(status=status)
    agentyper.set_pagination(
        total=total,
        returned=len(items),
        truncated=len(items) < total,
        has_more=len(items) < total,
    )
    return items


@app.command()
def get(
    contact_id: ResourceId = Argument(..., metavar="ID", help="Contact ID from `list`, e.g. c001"),
) -> dict[str, Any]:
    """Get full details for a single contact."""
    return agentyper.external_data(_get_or_exit(contact_id))


@app.command(mutating=True)
def create(
    name: str = Option(..., help="Full name (2–100 characters)"),
    email: str = Option(..., help="Email address — must be unique"),
    phone: str | None = Option(None, help="Phone in E.164 format, e.g. +1-555-0100"),
    tags: list[str] = Option([], help='Labels as JSON array, e.g. \'["vip","customer"]\''),  # noqa: B008
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a new contact. Pass --yes to skip confirmation in agent mode."""
    try:
        preview = store.build(name, email, phone, tags)  # validates; raises before confirm()
    except (InvalidField, DuplicateEmail) as e:
        _handle(e)

    if dry_run:
        return agentyper.result(preview, effect="would_create")

    if not agentyper.confirm(f"Create '{preview.name}' <{preview.email}>?", default=True):
        agentyper.echo("Aborted.")
        return agentyper.result(None, effect="noop")

    store.save(preview)
    log.info("created contact id=%s", preview.id)
    return agentyper.result(preview, effect="created")


@app.command(mutating=True)
def update(
    contact_id: ResourceId = Argument(..., metavar="ID", help="ID of the contact to update"),
    name: str | None = Option(None, help="New full name"),
    email: str | None = Option(None, help="New email — must be unique"),
    phone: str | None = Option(None, help="New phone in E.164 format"),
    status: Literal["active", "archived"] | None = Option(
        None, help="New status: active or archived"
    ),
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update a contact (partial — only supplied fields change). Use --dry-run to preview."""
    try:
        if dry_run:
            contact = store.get(contact_id)
            fields = {"name": name, "email": email, "phone": phone, "status": status}
            updated = contact.model_copy(update={k: v for k, v in fields.items() if v is not None})
            return agentyper.result(updated, effect="would_update")
        contact = store.update(contact_id, name=name, email=email, phone=phone, status=status)
        return agentyper.result(contact, effect="updated")
    except (ContactNotFound, InvalidField, DuplicateEmail) as e:
        _handle(e)


@app.command(mutating=True, danger_level="destructive")
def delete(
    contact_id: ResourceId = Argument(..., metavar="ID", help="ID of the contact to delete"),
    dry_run: bool = False,
) -> dict[str, Any]:
    """Permanently delete a contact. Pass --yes to confirm in agent mode."""
    contact = _get_or_exit(contact_id)

    if dry_run:
        return agentyper.result(contact, effect="would_delete")

    if not agentyper.confirm(
        f"Permanently delete '{contact.name}'? This cannot be undone.",
        default=False,  # agent must pass --yes explicitly; omitting --no is not consent
    ):
        agentyper.echo("Aborted.")
        return agentyper.result(None, effect="noop")

    store.delete(contact_id)
    log.info("deleted contact id=%s", contact_id)
    return agentyper.result(contact, effect="deleted")


@app.command(mutating=True, requires_editor=True, non_interactive_alternatives=["answers"])
def note(
    contact_id: ResourceId = Argument(..., metavar="ID", help="ID of the contact to annotate"),
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Add a free-text note. Opens $EDITOR in a terminal; use --answers in agent mode:

        contacts note c001 --answers '{"prompts":{"note_text":"Renewal due May 1"}}'
    """
    contact = _get_or_exit(contact_id)
    text = agentyper.prompt(
        "Note text",
        default=contact.note,
        param_name="note_text",
        alternatives=["answers"],
    )

    if dry_run:
        return agentyper.result(
            contact.model_copy(update={"note": text.strip()}), effect="would_update"
        )

    updated = store.set_note(contact_id, text)
    return agentyper.result(updated, effect="updated")


if __name__ == "__main__":
    app()
