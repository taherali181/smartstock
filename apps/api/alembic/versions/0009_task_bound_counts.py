"""task-bound cycle count execution

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE warehouse_tasks
          ADD COLUMN condition text NOT NULL DEFAULT 'sellable'
            CHECK (condition IN ('sellable','quarantined','damaged','expired')),
          ADD COLUMN ownership text NOT NULL DEFAULT 'owned',
          ADD COLUMN lot_id uuid,
          ADD COLUMN serial_id uuid,
          ADD COLUMN expected_position_version bigint
            CHECK (expected_position_version IS NULL OR expected_position_version >= 0),
          ADD FOREIGN KEY (organization_id, lot_id)
            REFERENCES lots(organization_id, id),
          ADD FOREIGN KEY (organization_id, serial_id)
            REFERENCES serial_numbers(organization_id, id),
          ADD CONSTRAINT warehouse_count_task_stock_identity CHECK (
            task_type <> 'count' OR (
              source_location_id IS NOT NULL
              AND product_id IS NOT NULL
              AND uom IS NOT NULL
              AND expected_position_version IS NOT NULL
            )
          );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE warehouse_tasks
          DROP CONSTRAINT IF EXISTS warehouse_count_task_stock_identity,
          DROP COLUMN IF EXISTS expected_position_version,
          DROP COLUMN IF EXISTS serial_id,
          DROP COLUMN IF EXISTS lot_id,
          DROP COLUMN IF EXISTS ownership,
          DROP COLUMN IF EXISTS condition;
        """
    )
