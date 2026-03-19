"""add quote target field to posts

Revision ID: 005
Revises: 004
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("quote_of_platform_post_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "quote_of_platform_post_id")
