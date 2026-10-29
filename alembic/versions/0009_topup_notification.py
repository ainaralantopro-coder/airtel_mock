"""Top-up notification : suivi de l'envoi à la banque sur les mouvements wallet

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("wallet_transaction") as batch:
        batch.add_column(sa.Column("topup_notification", sa.String(20), nullable=True))
        batch.add_column(sa.Column("topup_call_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_wallet_transaction_topup_call_id", "call_sent", ["topup_call_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("wallet_transaction") as batch:
        batch.drop_constraint("fk_wallet_transaction_topup_call_id", type_="foreignkey")
        batch.drop_column("topup_call_id")
        batch.drop_column("topup_notification")
