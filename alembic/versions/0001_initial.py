"""Schéma initial : customer, message_sent, access_token, call_sent, call_receive

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("msisdn", sa.String(9), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(20), nullable=False),
        sa.Column("is_barred", sa.Boolean(), nullable=False),
        sa.Column("is_pin_set", sa.Boolean(), nullable=False),
        sa.Column("dob", sa.DateTime(), nullable=False),
        sa.Column("account_status", sa.String(5), nullable=False),
        sa.Column("nationality", sa.String(5), nullable=False),
        sa.Column("id_number", sa.String(50), nullable=False),
        sa.Column("registration_status", sa.String(20), nullable=False),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("opted_in_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_msisdn", "customer", ["msisdn"], unique=True)

    op.create_table(
        "message_sent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("msisdn", sa.String(9), nullable=False),
        sa.Column("message_en", sa.Text(), nullable=True),
        sa.Column("message_fr", sa.Text(), nullable=True),
        sa.Column("message_es", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_message_sent_customer_id", "message_sent", ["customer_id"])
    op.create_index("ix_message_sent_msisdn", "message_sent", ["msisdn"])

    op.create_table(
        "access_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_access_token_token", "access_token", ["token"], unique=True)

    op.create_table(
        "call_sent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("flow", sa.String(50), nullable=False),
        sa.Column("msisdn", sa.String(9), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("request_headers", JsonType, nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_headers", JsonType, nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_call_sent_flow", "call_sent", ["flow"])
    op.create_index("ix_call_sent_msisdn", "call_sent", ["msisdn"])

    op.create_table(
        "call_receive",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("query_string", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("request_headers", JsonType, nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_call_receive_path", "call_receive", ["path"])


def downgrade() -> None:
    op.drop_table("call_receive")
    op.drop_table("call_sent")
    op.drop_table("access_token")
    op.drop_table("message_sent")
    op.drop_table("customer")
