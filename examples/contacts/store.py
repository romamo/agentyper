"""
Business logic for the contacts example.

No dependency on agentyper or any CLI framework.
The store raises typed exceptions; the CLI layer translates them into
structured agentyper errors. This separation means the same store could
be used behind a REST API, an MCP server, or a test suite with no changes.
"""

from __future__ import annotations

import re
import uuid

from models import Contact, ContactSummary

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Domain exceptions  (no exit codes, no JSON — that belongs to the CLI layer)
# ---------------------------------------------------------------------------


class ContactNotFound(Exception):
    def __init__(self, contact_id: str) -> None:
        self.contact_id = contact_id
        super().__init__(f"Contact '{contact_id}' not found")


class DuplicateEmail(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"A contact with email '{email}' already exists")


class InvalidField(Exception):
    def __init__(self, field: str, message: str, constraint: str | None = None) -> None:
        self.field = field
        self.constraint = constraint
        super().__init__(message)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class ContactStore:
    """
    In-memory contact storage.

    Replace _db and the methods with your real DB/API calls — the CLI layer
    and its agent ergonomics stay identical.
    """

    def __init__(self) -> None:
        self._db: dict[str, Contact] = {
            "c001": Contact(
                id="c001",
                name="Alice Chen",
                email="alice@example.com",
                phone="+1-555-0100",
                tags=["vip", "customer"],
            ),
            "c002": Contact(
                id="c002",
                name="Bob Smith",
                email="bob@example.com",
                tags=["prospect"],
            ),
            "c003": Contact(
                id="c003",
                name="Carol Davis",
                email="carol@example.com",
                phone="+1-555-0102",
                status="archived",
            ),
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list(
        self,
        status: str = "active",
        limit: int = 20,
    ) -> list[ContactSummary]:
        records = list(self._db.values())
        if status != "all":
            records = [c for c in records if c.status == status]
        return [
            ContactSummary(id=c.id, name=c.name, email=c.email, status=c.status)
            for c in records[:limit]
        ]

    def count(self, status: str = "active") -> int:
        """Return the total number of contacts matching the given status filter."""
        records = list(self._db.values())
        if status != "all":
            records = [c for c in records if c.status == status]
        return len(records)

    def get(self, contact_id: str) -> Contact:
        contact = self._db.get(contact_id)
        if contact is None:
            raise ContactNotFound(contact_id)
        return contact

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def build(
        self,
        name: str,
        email: str,
        phone: str | None = None,
        tags: list[str] | None = None,
    ) -> Contact:
        """Validate inputs and return an unsaved Contact (used for dry-run preview)."""
        self._validate_name(name)
        self._validate_email(email)
        if any(c.email == email for c in self._db.values()):
            raise DuplicateEmail(email)
        return Contact(
            id=f"c{uuid.uuid4().hex[:6]}",
            name=name.strip(),
            email=email,
            phone=phone,
            tags=tags or [],
        )

    def create(
        self,
        name: str,
        email: str,
        phone: str | None = None,
        tags: list[str] | None = None,
    ) -> Contact:
        return self.save(self.build(name, email, phone, tags))

    def update(
        self,
        contact_id: str,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        status: str | None = None,
    ) -> Contact:
        contact = self.get(contact_id)

        if email is not None:
            self._validate_email(email)
            if any(c.email == email and c.id != contact_id for c in self._db.values()):
                raise DuplicateEmail(email)

        updated = contact.model_copy(
            update={
                k: v
                for k, v in {"name": name, "email": email, "phone": phone, "status": status}.items()
                if v is not None
            }
        )
        self._db[contact_id] = updated
        return updated

    def save(self, contact: Contact) -> Contact:
        """Persist a pre-built contact (e.g. from build()) without re-validating."""
        self._db[contact.id] = contact
        return contact

    def delete(self, contact_id: str) -> None:
        self.get(contact_id)  # raises ContactNotFound if missing
        del self._db[contact_id]

    def set_note(self, contact_id: str, text: str) -> Contact:
        contact = self.get(contact_id)
        updated = contact.model_copy(update={"note": text.strip()})
        self._db[contact_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Internal validation
    # ------------------------------------------------------------------

    def _validate_name(self, name: str) -> None:
        if len(name.strip()) < 2:
            raise InvalidField("name", "name must be at least 2 characters", "minLength: 2")

    def _validate_email(self, email: str) -> None:
        if not _EMAIL_RE.match(email):
            raise InvalidField(
                "email",
                f"'{email}' is not a valid email address",
                r"pattern: ^[^@\s]+@[^@\s]+\.[^@\s]+$",
            )
