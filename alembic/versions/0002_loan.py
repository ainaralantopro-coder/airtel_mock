"""Table loan (flux Apply Loan)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("msisdn", sa.String(9), nullable=False),
        sa.Column("transaction_id", sa.String(50), nullable=False),
        sa.Column("requested_amount", sa.Integer(), nullable=False),
        sa.Column("fees_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_code", sa.String(20), nullable=True),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("loan_id", sa.String(50), nullable=True),
        sa.Column("loan_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("loan_fees", sa.Numeric(18, 2), nullable=True),
        sa.Column("outstanding_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column("tenure_id", sa.String(20), nullable=True),
        sa.Column("tenure_name", sa.String(100), nullable=True),
        sa.Column("interest_rate", sa.String(20), nullable=True),
        sa.Column("is_disbursed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_loan_msisdn", "loan", ["msisdn"])
    op.create_index("ix_loan_transaction_id", "loan", ["transaction_id"], unique=True)
    op.create_index("ix_loan_status", "loan", ["status"])
    op.create_index("ix_loan_loan_id", "loan", ["loan_id"])


def downgrade() -> None:
    op.drop_table("loan")
