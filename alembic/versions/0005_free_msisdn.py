"""MSISDN en champ libre : colonnes msisdn élargies de 9 à 50 caractères

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("customer", "message_sent", "loan", "call_sent")


def upgrade() -> None:
    for table in TABLES:
        # batch : recrée la table sous SQLite (tests), simple ALTER COLUMN sous PostgreSQL
        with op.batch_alter_table(table) as batch:
            batch.alter_column("msisdn", type_=sa.String(50), existing_type=sa.String(9))


def downgrade() -> None:
    # Échoue si des MSISDN de plus de 9 caractères ont été enregistrés
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("msisdn", type_=sa.String(9), existing_type=sa.String(50))
