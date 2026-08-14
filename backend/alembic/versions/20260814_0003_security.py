"""Add dashboard sessions and retry-safe approval execution.

Revision ID: 20260814_0003
Revises: 20260814_0002
Create Date: 2026-08-14
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = "20260814_0003"
down_revision = "20260814_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dashboard_users_username", "dashboard_users", ["username"], unique=True)
    op.create_table(
        "dashboard_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("csrf_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["dashboard_users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_dashboard_sessions_user_id", "dashboard_sessions", ["user_id"])
    op.create_index("ix_dashboard_sessions_token_hash", "dashboard_sessions", ["token_hash"], unique=True)
    op.create_index("ix_dashboard_sessions_expires_at", "dashboard_sessions", ["expires_at"])
    op.create_table(
        "login_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("remote_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_audit_username", "login_audit", ["username"])

    op.add_column("scheduled_posts", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.create_index("ix_scheduled_posts_idempotency_key", "scheduled_posts", ["idempotency_key"], unique=True)

    op.add_column("approval_requests", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.add_column("approval_requests", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("approval_requests", sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval_requests", sa.Column("execution_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval_requests", sa.Column("last_error", sa.Text(), nullable=True))
    connection = op.get_bind()
    approval_ids = connection.execute(sa.text("SELECT id FROM approval_requests")).scalars().all()
    for approval_id in approval_ids:
        connection.execute(
            sa.text("UPDATE approval_requests SET idempotency_key = :key WHERE id = :id"),
            {"key": str(uuid.uuid4()), "id": approval_id},
        )
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.alter_column("idempotency_key", existing_type=sa.String(), nullable=False)
    op.create_index("ix_approval_requests_idempotency_key", "approval_requests", ["idempotency_key"], unique=True)
    op.create_table(
        "approval_execution_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("approval_id", sa.String(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("approval_id", "attempt_number"),
    )
    op.create_index("ix_approval_execution_attempts_approval_id", "approval_execution_attempts", ["approval_id"])
    op.create_index(
        "ix_approval_execution_attempts_idempotency_key",
        "approval_execution_attempts",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_table("approval_execution_attempts")
    op.drop_index("ix_scheduled_posts_idempotency_key", table_name="scheduled_posts")
    op.drop_column("scheduled_posts", "idempotency_key")
    op.drop_index("ix_approval_requests_idempotency_key", table_name="approval_requests")
    with op.batch_alter_table("approval_requests") as batch_op:
        for column in (
            "last_error",
            "execution_finished_at",
            "execution_started_at",
            "attempt_count",
            "idempotency_key",
        ):
            batch_op.drop_column(column)
    op.drop_table("login_audit")
    op.drop_table("dashboard_sessions")
    op.drop_table("dashboard_users")
