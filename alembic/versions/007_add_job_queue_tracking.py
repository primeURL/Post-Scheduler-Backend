"""add durable job queue tracking

Revision ID: 007
Revises: 006
Create Date: 2026-03-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


job_type_enum = postgresql.ENUM("publish", "analytics", name="job_type", create_type=False)
job_status_enum = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "failed",
    "dead_letter",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE job_type AS ENUM ('publish', 'analytics');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE job_status AS ENUM ('queued', 'running', 'completed', 'failed', 'dead_letter');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    inspector = sa.inspect(bind)
    if inspector.has_table("job_queue"):
        op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_post_id ON job_queue (post_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_job_type ON job_queue (job_type)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_status ON job_queue (status)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_next_retry_at ON job_queue (next_retry_at)")
        return

    op.create_table(
        "job_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", job_type_enum, nullable=False),
        sa.Column("status", job_status_enum, nullable=False, server_default="queued"),
        sa.Column("arq_task_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("arq_task_id", name="uq_job_queue_arq_task_id"),
    )

    op.create_index("ix_job_queue_post_id", "job_queue", ["post_id"], unique=False)
    op.create_index("ix_job_queue_job_type", "job_queue", ["job_type"], unique=False)
    op.create_index("ix_job_queue_status", "job_queue", ["status"], unique=False)
    op.create_index("ix_job_queue_next_retry_at", "job_queue", ["next_retry_at"], unique=False)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_job_queue_next_retry_at")
    op.execute("DROP INDEX IF EXISTS ix_job_queue_status")
    op.execute("DROP INDEX IF EXISTS ix_job_queue_job_type")
    op.execute("DROP INDEX IF EXISTS ix_job_queue_post_id")
    op.execute("DROP TABLE IF EXISTS job_queue")

    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP TYPE IF EXISTS job_type")
