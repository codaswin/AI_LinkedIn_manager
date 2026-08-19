"""Add per-user ownership (user_id) to previously single-tenant tables.

Stage 1 of the multi-tenant conversion (see plans/peaceful-scribbling-tiger.md):
platform_credentials, brand_voices, approval_requests, scheduled_posts,
processed_notifications, learning_proposals, and feedback each get a
`user_id` FK to dashboard_users, backfilled to whichever user owned the
data before this migration (the DASHBOARD_ADMIN_USERNAME bootstrap admin,
or any existing dashboard user as a fallback).

Only platform_credentials is tightened to NOT NULL here — its entire
application-code write path (app.memory.platform_credentials) was updated
in this same stage to always stamp user_id. The other six tables' write
paths (approval_gate.py, brand_voice.py, app.automation,
proposal_review.py, feedback capture) are Stage 2/3 work and don't
populate user_id yet, so tightening them now would break every existing
insert. Their columns are added + backfilled here (so data ownership is
preserved once Stage 2/3 lands) but stay nullable until then.

platform_credentials.id additionally gets rewritten from
`f"{platform_id}:{field_name}"` to `f"{user_id}:{platform_id}:{field_name}"`
— its old PK shape would collide once more than one user saves the same
platform+field.

agent_settings is intentionally NOT touched here — its conversion to a
per-user composite (user_id, key) PK is deferred to the Stage 3 migration
that also updates its only real callers (the scheduler's research job,
agents/research.py).

Revision ID: 20260819_0004
Revises: 20260814_0003
Create Date: 2026-08-19
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa

revision = "20260819_0004"
down_revision = "20260814_0003"
branch_labels = None
depends_on = None

_NOT_NULL_TABLES = ("platform_credentials",)
_NULLABLE_TABLES = (
    "brand_voices",
    "approval_requests",
    "scheduled_posts",
    "processed_notifications",
    "learning_proposals",
    "feedback",
)
_TABLES = _NOT_NULL_TABLES + _NULLABLE_TABLES


def _resolve_backfill_owner(connection: sa.engine.Connection) -> str | None:
    admin_username = os.environ.get("DASHBOARD_ADMIN_USERNAME", "").strip().lower()
    if admin_username:
        admin_id = connection.execute(
            sa.text("SELECT id FROM dashboard_users WHERE username = :username"),
            {"username": admin_username},
        ).scalar()
        if admin_id is not None:
            return str(admin_id)
    # Fresh install with no bootstrap admin yet (it's created at app startup,
    # after migrations run) — fall back to any existing dashboard user, if
    # one somehow already exists. If not, and a NOT-NULL table below turns
    # out to have pre-existing rows to own, that's a real error, not
    # silently skippable data loss (see the raise below).
    fallback_id = connection.execute(sa.text("SELECT id FROM dashboard_users LIMIT 1")).scalar()
    return str(fallback_id) if fallback_id is not None else None


def upgrade() -> None:
    connection = op.get_bind()
    owner_id = _resolve_backfill_owner(connection)

    for table in _TABLES:
        op.add_column(table, sa.Column("user_id", sa.String(), nullable=True))
        row_count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        if row_count and owner_id is not None:
            connection.execute(
                sa.text(f"UPDATE {table} SET user_id = :owner_id WHERE user_id IS NULL"),
                {"owner_id": owner_id},
            )
        elif row_count and table in _NOT_NULL_TABLES:
            raise RuntimeError(
                f"Cannot migrate {table!r}: it has {row_count} existing row(s) but no "
                "dashboard user exists to own them. Set DASHBOARD_ADMIN_USERNAME to an "
                "account that has already been bootstrapped at least once, then re-run."
            )
        with op.batch_alter_table(table) as batch_op:
            if table in _NOT_NULL_TABLES:
                batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
            batch_op.create_foreign_key(
                f"fk_{table}_user_id", "dashboard_users", ["user_id"], ["id"], ondelete="CASCADE"
            )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # Rewrite platform_credentials' composed-string PK to include user_id
    # now that every row has one.
    connection.execute(
        sa.text("UPDATE platform_credentials SET id = user_id || ':' || platform_id || ':' || field_name")
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE platform_credentials SET id = platform_id || ':' || field_name"))

    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_user_id", type_="foreignkey")
            batch_op.drop_column("user_id")
