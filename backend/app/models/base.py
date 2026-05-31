"""
TENANT ISOLATION CONTRACT
=========================
Every database table in this application MUST inherit from TenantBase.

RULES (violation = data breach):
  Rule 1 — Schema:  Every table has a non-nullable tenant_id column.
  Rule 2 — Writes:  tenant_id is always set from the JWT, never from user input.
  Rule 3 — Reads:   Every SELECT query MUST include WHERE tenant_id = :tenant_id.
  Rule 4 — Deletes: Soft or hard deletes MUST verify tenant_id before deletion.

These rules are enforced at the CRUD/service layer and tested in
tests/test_tenant_isolation.py.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantBase(SQLModel):
    """Base class for all tenant-isolated database tables.

    Provides: primary key, tenant isolation key, and audit timestamps.
    Do NOT instantiate directly — subclass with `table=True`.

    NOTE: We intentionally do NOT use sa_column=Column(...) here because
    a SQLAlchemy Column object can only be assigned to a single Table.
    Sharing one Column instance across multiple subclasses causes:
      "Column object 'created_at' already assigned to Table '...'"
    Instead we use sa_column_kwargs so SQLModel creates a fresh Column
    per concrete table while honouring our type/server_default/onupdate.
    """

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        description="Surrogate primary key",
    )
    tenant_id: uuid.UUID = Field(
        index=True,
        nullable=False,
        description="Tenant isolation key. ALL queries MUST filter by this.",
    )
    created_at: Optional[datetime] = Field(
        default_factory=utc_now,
        nullable=False,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"server_default": "now()"},
    )
    updated_at: Optional[datetime] = Field(
        default_factory=utc_now,
        nullable=False,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"server_default": "now()", "onupdate": utc_now},
    )
