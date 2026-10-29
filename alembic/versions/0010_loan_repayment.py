"""Table loan_repayment (flux Manual repay loan)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan_repayment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("borrower_msisdn", sa.String(50), nullable=False),
        sa.Column("payer_msisdn", sa.String(50), nullable=False),
        sa.Column("payer_customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("loan_ref", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("transaction_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_code", sa.String(20), nullable=True),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("total_amt_paid", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_outstanding_amt", sa.Numeric(18, 2), nullable=True),
        sa.Column("loan_fees", sa.Numeric(18, 2), nullable=True),
        sa.Column("wallet_transaction_id", sa.Integer(), sa.ForeignKey("wallet_transaction.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_loan_repayment_borrower_msisdn", "loan_repayment", ["borrower_msisdn"])
    op.create_index("ix_loan_repayment_payer_msisdn", "loan_repayment", ["payer_msisdn"])
    op.create_index("ix_loan_repayment_transaction_id", "loan_repayment", ["transaction_id"], unique=True)


def downgrade() -> None:
    op.drop_table("loan_repayment")
