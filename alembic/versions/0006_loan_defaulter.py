"""Table loan_defaulter (flux Set defaulter status)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan_defaulter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("msisdn", sa.String(50), nullable=False),
        sa.Column("loan_ref", sa.String(50), nullable=True),
        sa.Column("loan_type", sa.String(20), nullable=False),
        sa.Column("loan_pk", sa.Integer(), sa.ForeignKey("loan.id"), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("is_defaulter", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_loan_defaulter_customer_id", "loan_defaulter", ["customer_id"])
    op.create_index("ix_loan_defaulter_msisdn", "loan_defaulter", ["msisdn"])


def downgrade() -> None:
    op.drop_table("loan_defaulter")
