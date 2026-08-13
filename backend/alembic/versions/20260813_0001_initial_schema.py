"""Initial production schema baseline.

Revision ID: 20260813_0001
Revises:
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260813_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("agent_settings", sa.Column("key", sa.String(), primary_key=True), sa.Column("value", sa.String(), nullable=False), sa.Column("updated_by", sa.String(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("approval_requests", sa.Column("id", sa.String(), primary_key=True), sa.Column("tool_name", sa.String(), nullable=False), sa.Column("arguments", sa.JSON(), nullable=False), sa.Column("requested_by_agent", sa.String(), nullable=False), sa.Column("reason", sa.String(), nullable=False), sa.Column("confidence", sa.Float(), nullable=True), sa.Column("status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True), sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True), sa.Column("decided_by", sa.String(), nullable=True))
    op.create_table("brand_voices", sa.Column("id", sa.String(), primary_key=True), sa.Column("title", sa.String(), nullable=False), sa.Column("content", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("feedback", sa.Column("id", sa.String(), primary_key=True), sa.Column("task_id", sa.String(), nullable=False), sa.Column("agent_name", sa.String(), nullable=False), sa.Column("signal_type", sa.String(), nullable=False), sa.Column("detail", sa.String(), nullable=False), sa.Column("engagement_stats", sa.JSON(), nullable=True), sa.Column("confidence", sa.Float(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("learning_proposals", sa.Column("id", sa.String(), primary_key=True), sa.Column("pattern", sa.String(), nullable=False), sa.Column("change_type", sa.String(), nullable=False), sa.Column("proposed_change", sa.String(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True), sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True), sa.Column("decided_by", sa.String(), nullable=True))
    op.create_table("platform_credentials", sa.Column("id", sa.String(), primary_key=True), sa.Column("platform_id", sa.String(), nullable=False), sa.Column("field_name", sa.String(), nullable=False), sa.Column("encrypted_value", sa.String(), nullable=False), sa.Column("masked_preview", sa.String(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("post_episodes", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_id", sa.String(), nullable=False), sa.Column("post_id", sa.String(), nullable=False, unique=True), sa.Column("content", sa.String(), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.Column("impressions", sa.Integer(), nullable=False), sa.Column("likes", sa.Integer(), nullable=False), sa.Column("comments", sa.Integer(), nullable=False), sa.Column("shares", sa.Integer(), nullable=False), sa.Column("follower_delta", sa.Integer(), nullable=False), sa.Column("archived", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("semantic_memory_records", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("vector_id", sa.Integer(), nullable=False, unique=True), sa.Column("entity_id", sa.String(), nullable=False), sa.Column("category", sa.String(), nullable=False), sa.Column("fact", sa.String(), nullable=False), sa.Column("source", sa.String(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("thread_episodes", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_id", sa.String(), nullable=False), sa.Column("thread_id", sa.String(), nullable=False, unique=True), sa.Column("thread_type", sa.String(), nullable=False), sa.Column("content", sa.String(), nullable=True), sa.Column("participants", sa.JSON(), nullable=False), sa.Column("resolution", sa.String(), nullable=True), sa.Column("important", sa.Boolean(), nullable=False), sa.Column("content_purged", sa.Boolean(), nullable=False), sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    for table, columns in {"approval_requests": ["status", "tool_name"], "feedback": ["task_id", "agent_name", "signal_type", "created_at"], "learning_proposals": ["change_type", "status"], "platform_credentials": ["platform_id"], "post_episodes": ["entity_id", "post_id"], "semantic_memory_records": ["vector_id", "entity_id", "category"], "thread_episodes": ["entity_id", "thread_id"]}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("thread_episodes", "semantic_memory_records", "post_episodes", "platform_credentials", "learning_proposals", "feedback", "brand_voices", "approval_requests", "agent_settings"):
        op.drop_table(table)
