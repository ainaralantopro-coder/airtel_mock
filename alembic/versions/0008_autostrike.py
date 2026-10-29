"""Table autostrike (flux Autostrike request)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autostrike",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.String(100), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("msisdn", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("country", sa.String(10), nullable=True),
        sa.Column("status", sa.String(5), nullable=False),
        sa.Column("reference_id", sa.String(20), nullable=False, unique=True),
        sa.Column("airtel_money_id", sa.String(150), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("wallet_transaction_id", sa.Integer(), sa.ForeignKey("wallet_transaction.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_autostrike_transaction_id", "autostrike", ["transaction_id"], unique=True)
    op.create_index("ix_autostrike_customer_id", "autostrike", ["customer_id"])
    op.create_index("ix_autostrike_msisdn", "autostrike", ["msisdn"])


def downgrade() -> None:
    op.drop_table("autostrike")
