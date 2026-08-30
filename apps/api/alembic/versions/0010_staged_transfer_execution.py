"""staged warehouse transfer execution

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE warehouse_tasks
          ADD COLUMN destination_warehouse_id uuid;

        UPDATE warehouse_tasks
        SET state='cancelled',version=version+1,updated_at=now()
        WHERE task_type='transfer' AND state <> 'cancelled' AND (
          destination_warehouse_id IS NULL
          OR source_location_id IS NULL OR destination_location_id IS NULL
          OR product_id IS NULL OR quantity IS NULL OR uom IS NULL
          OR expected_position_version IS NULL
        );

        ALTER TABLE warehouse_tasks
          ADD FOREIGN KEY (organization_id, destination_warehouse_id)
            REFERENCES warehouses(organization_id, id),
          ADD CONSTRAINT warehouse_transfer_task_stock_identity CHECK (
            task_type <> 'transfer' OR state = 'cancelled'
            OR reference_type = 'transfer_receipt' OR (
              destination_warehouse_id IS NOT NULL
              AND destination_warehouse_id <> warehouse_id
              AND source_location_id IS NOT NULL
              AND destination_location_id IS NOT NULL
              AND product_id IS NOT NULL
              AND quantity IS NOT NULL
              AND uom IS NOT NULL
              AND expected_position_version IS NOT NULL
            )
          );

        ALTER TABLE transfer_lines
          ADD COLUMN condition text NOT NULL DEFAULT 'sellable'
            CHECK (condition IN ('sellable','quarantined','damaged','expired')),
          ADD COLUMN ownership text NOT NULL DEFAULT 'owned',
          ADD COLUMN unit_cost numeric(28,9) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE transfer_lines
          DROP COLUMN IF EXISTS unit_cost,
          DROP COLUMN IF EXISTS ownership,
          DROP COLUMN IF EXISTS condition;
        ALTER TABLE warehouse_tasks
          DROP CONSTRAINT IF EXISTS warehouse_transfer_task_stock_identity,
          DROP COLUMN IF EXISTS destination_warehouse_id;
        """
    )
