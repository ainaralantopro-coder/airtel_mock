"""Décaissement des prêts (flux Confirm Loan)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("loan", sa.Column("external_transaction_id", sa.String(50), nullable=True))
    op.add_column("loan", sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("loan", "disbursed_at")
    op.drop_column("loan", "external_transaction_id")
