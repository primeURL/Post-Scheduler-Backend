"""drop obsolete job_queue unique constraint

Revision ID: 008
Revises: 007
Create Date: 2026-03-28 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE constraint_schema = 'public'
                  AND table_name = 'job_queue'
                  AND constraint_name = 'uq_job_queue_post_type_status'
            ) THEN
                ALTER TABLE job_queue DROP CONSTRAINT uq_job_queue_post_type_status;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE constraint_schema = 'public'
                  AND table_name = 'job_queue'
                  AND constraint_name = 'uq_job_queue_post_type_status'
            ) THEN
                ALTER TABLE job_queue
                ADD CONSTRAINT uq_job_queue_post_type_status
                UNIQUE (post_id, job_type, status);
            END IF;
        END $$;
        """
    )
