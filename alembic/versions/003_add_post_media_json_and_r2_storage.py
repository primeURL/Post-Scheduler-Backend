"""add post media json column

Revision ID: 003
Revises: 002
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("media", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "media")
