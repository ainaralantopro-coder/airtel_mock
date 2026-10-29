"""Wallet des clients : solde et mouvements (dépôt, retrait)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customer", sa.Column("balance", sa.Numeric(18, 2), server_default="0", nullable=False))
    op.create_table(
        "wallet_transaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("msisdn", sa.String(50), nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wallet_transaction_customer_id", "wallet_transaction", ["customer_id"])
    op.create_index("ix_wallet_transaction_msisdn", "wallet_transaction", ["msisdn"])
    op.create_index("ix_wallet_transaction_reference", "wallet_transaction", ["reference"], unique=True)


def downgrade() -> None:
    op.drop_table("wallet_transaction")
    op.drop_column("customer", "balance")
