"""Tighten user_id to NOT NULL on the four tables whose write paths Stage 3
of the multi-tenant conversion (see plans/peaceful-scribbling-tiger.md)
completed: scheduled_posts (tools/schedule_post.py), processed_notifications
(learning/scheduler.py's engagement job), learning_proposals
(learning/proposal_review.py), and feedback (learning/feedback.py). Every
INSERT through each of these modules now always stamps user_id, so the
nullable escape hatch added in 20260819_0004 is no longer needed for them.

Any pre-existing NULL rows (e.g. processed_notifications written by an
engagement job run before this stage landed) are backfilled to the same
owner resolution used by 20260819_0004/0006, so the NOT NULL tightening
below never fails against real data.

Revision ID: 20260819_0007
Revises: 20260819_0006
Create Date: 2026-08-19
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa

revision = "20260819_0007"
down_revision = "20260819_0006"
branch_labels = None
depends_on = None

_TABLES = ("scheduled_posts", "processed_notifications", "learning_proposals", "feedback")


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
    owner_id: str | None = None
    for table in _TABLES:
        orphaned = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")).scalar() or 0
        if orphaned:
            owner_id = owner_id or _resolve_backfill_owner(connection)
            if owner_id is None:
                raise RuntimeError(
                    f"Cannot migrate {table!r}: it has {orphaned} row(s) with no owner and no "
                    "dashboard user exists to backfill them. Set DASHBOARD_ADMIN_USERNAME to an "
                    "account that has already been bootstrapped at least once, then re-run."
                )
            connection.execute(
                sa.text(f"UPDATE {table} SET user_id = :owner_id WHERE user_id IS NULL"), {"owner_id": owner_id}
            )
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)
