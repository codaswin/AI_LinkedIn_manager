"""Tighten user_id to NOT NULL on the two tables whose write paths Stage 2
of the multi-tenant conversion (see plans/peaceful-scribbling-tiger.md)
completed: approval_requests (safety/approval_gate.py) and brand_voices
(memory/brand_voice.py). Every INSERT through either module now always
stamps user_id, so the nullable escape hatch added in 20260819_0004 is no
longer needed for these two tables.

scheduled_posts, processed_notifications, learning_proposals, and feedback
stay nullable — their write paths are still Stage 3+ work.

Revision ID: 20260819_0005
Revises: 20260819_0004
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260819_0005"
down_revision = "20260819_0004"
branch_labels = None
depends_on = None

_TABLES = ("approval_requests", "brand_voices")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)
