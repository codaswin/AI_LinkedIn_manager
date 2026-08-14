"""Add durable automation queues.

Revision ID: 20260814_0002
Revises: 20260813_0001
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260814_0002"
down_revision = "20260813_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scheduled_posts_publish_at", "scheduled_posts", ["publish_at"])
    op.create_index("ix_scheduled_posts_status", "scheduled_posts", ["status"])
    op.create_table(
        "processed_notifications",
        sa.Column("notification_id", sa.String(), primary_key=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("processed_notifications")
    op.drop_index("ix_scheduled_posts_status", table_name="scheduled_posts")
    op.drop_index("ix_scheduled_posts_publish_at", table_name="scheduled_posts")
    op.drop_table("scheduled_posts")
