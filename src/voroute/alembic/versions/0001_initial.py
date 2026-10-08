"""Create merchants, users, orders, jobs, and outcomes.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("api_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("api_token_hash"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("merchant_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_merchant_id", "users", ["merchant_id"])
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("merchant_id", sa.String(length=36), nullable=False),
        sa.Column("external_order_id", sa.String(length=200), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("cod", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.UniqueConstraint(
            "merchant_id", "external_order_id", name="uq_orders_merchant_external"
        ),
    )
    op.create_index("ix_orders_merchant_id", "orders", ["merchant_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("merchant_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("speech_result", sa.String(length=32), nullable=False),
        sa.Column("capture_path", sa.String(length=32), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("keypad_offered", sa.Boolean(), nullable=False),
        sa.Column("call_sid", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
    )
    op.create_index("ix_jobs_merchant_id", "jobs", ["merchant_id"])
    op.create_index("ix_jobs_order_id", "jobs", ["order_id"])
    op.create_table(
        "outcomes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("merchant_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capture_path", sa.String(length=32), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_outcomes_merchant_id", "outcomes", ["merchant_id"])
    op.create_index("ix_outcomes_order_id", "outcomes", ["order_id"])


def downgrade() -> None:
    op.drop_table("outcomes")
    op.drop_table("jobs")
    op.drop_table("orders")
    op.drop_table("users")
    op.drop_table("merchants")
