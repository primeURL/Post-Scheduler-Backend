"""add subscription_type and avatar_url to connected_accounts

Revision ID: 002
Revises: 001
Create Date: 2026-03-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connected_accounts",
        sa.Column("subscription_type", sa.String(50), nullable=True),
    )
    op.add_column(
        "connected_accounts",
        sa.Column("avatar_url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connected_accounts", "avatar_url")
    op.drop_column("connected_accounts", "subscription_type")
