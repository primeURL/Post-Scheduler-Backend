"""add soft delete fields and extended analytics counters

Revision ID: 004
Revises: 003
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("posts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_posts_is_deleted", "posts", ["is_deleted"], unique=False)

    op.add_column("post_analytics", sa.Column("quoted_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("post_analytics", sa.Column("bookmarks", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("post_analytics", "bookmarks")
    op.drop_column("post_analytics", "quoted_count")

    op.drop_index("ix_posts_is_deleted", table_name="posts")
    op.drop_column("posts", "deleted_at")
    op.drop_column("posts", "is_deleted")
