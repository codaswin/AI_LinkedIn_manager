"""agent_settings becomes per-user: composite (user_id, key) PK instead of
a bare `key` PK — Stage 3 of the multi-tenant conversion (see
plans/peaceful-scribbling-tiger.md), landed together with the scheduler's
per-user fan-out and research.py's per-user automation-topic setting,
which are its only real callers besides the generic /settings/{key}
endpoint.

Existing rows are backfilled to the same owner resolution used by
20260819_0004 (DASHBOARD_ADMIN_USERNAME, falling back to any existing
dashboard user). The table is rebuilt rather than PK-altered in place
(op.batch_alter_table's drop_constraint needs a constraint name that isn't
portable between the Postgres and SQLite backends this app runs against;
a plain create-copy-drop-rename sidesteps that entirely and is simple
enough for a settings table with, at most, a handful of rows per user).

Revision ID: 20260819_0006
Revises: 20260819_0005
Create Date: 2026-08-19
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa

revision = "20260819_0006"
down_revision = "20260819_0005"
branch_labels = None
depends_on = None


def _resolve_backfill_owner(connection: sa.engine.Connection) -> str | None:
    admin_username = os.environ.get("DASHBOARD_ADMIN_USERNAME", "").strip().lower()
    if admin_username:
        admin_id = connection.execute(
            sa.text("SELECT id FROM dashboard_users WHERE username = :username"),
            {"username": admin_username},
        ).scalar()
        if admin_id is not None:
            return str(admin_id)
    fallback_id = connection.execute(sa.text("SELECT id FROM dashboard_users LIMIT 1")).scalar()
    return str(fallback_id) if fallback_id is not None else None


def upgrade() -> None:
    connection = op.get_bind()
    row_count = connection.execute(sa.text("SELECT COUNT(*) FROM agent_settings")).scalar() or 0
    owner_id = _resolve_backfill_owner(connection) if row_count else None
    if row_count and owner_id is None:
        raise RuntimeError(
            "Cannot migrate agent_settings: it has existing row(s) but no dashboard user exists to "
            "own them. Set DASHBOARD_ADMIN_USERNAME to an account that has already been "
            "bootstrapped at least once, then re-run."
        )

    op.create_table(
        "agent_settings_new",
        sa.Column("user_id", sa.String(), sa.ForeignKey("dashboard_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    if row_count:
        connection.execute(
            sa.text(
                "INSERT INTO agent_settings_new (user_id, key, value, updated_by, updated_at) "
                "SELECT :owner_id, key, value, updated_by, updated_at FROM agent_settings"
            ),
            {"owner_id": owner_id},
        )
    op.drop_table("agent_settings")
    op.rename_table("agent_settings_new", "agent_settings")
    op.create_index("ix_agent_settings_user_id", "agent_settings", ["user_id"])


def downgrade() -> None:
    connection = op.get_bind()
    op.drop_index("ix_agent_settings_user_id", table_name="agent_settings")
    op.create_table(
        "agent_settings_old",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Collapses back to one row per key — if more than one user had set the
    # same key, only one arbitrarily wins, which is the best a downgrade to
    # the old single-tenant shape can do.
    connection.execute(
        sa.text(
            "INSERT INTO agent_settings_old (key, value, updated_by, updated_at) "
            "SELECT key, value, updated_by, updated_at FROM agent_settings GROUP BY key"
        )
    )
    op.drop_table("agent_settings")
    op.rename_table("agent_settings_old", "agent_settings")
