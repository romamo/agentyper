"""
Domain models for the contacts example.

Pure Pydantic — zero dependency on agentyper or any CLI framework.
These models serve three purposes simultaneously:
  1. Runtime validation (Pydantic)
  2. JSON serialisation for --format json output (automatic via model_dump)
  3. JSON Schema generation for --schema output_schema (automatic via TypeAdapter)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Contact(BaseModel):
    """Full contact record."""

    id: str = Field(description="Unique contact ID")
    name: str = Field(description="Full name")
    email: str = Field(description="Primary email address")
    phone: str | None = Field(None, description="Phone in E.164 format, e.g. +1-555-0100")
    tags: list[str] = Field(default_factory=list, description="Searchable labels")
    status: Literal["active", "archived"] = Field("active", description="Lifecycle status")
    note: str | None = Field(None, description="Free-text note")


class ContactSummary(BaseModel):
    """Compact representation for list views — omits large/optional fields.

    Using a summary model on list commands is a key context-window discipline
    pattern: agents receive only the fields they need to identify and select
    records, and call `get` when they need full details.
    """

    id: str
    name: str
    email: str
    status: Literal["active", "archived"]
