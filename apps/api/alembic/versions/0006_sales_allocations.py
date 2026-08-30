"""inventory-backed sales allocations

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_POLICY = """
USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sales_allocations (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          sales_order_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'posted' CHECK (state IN ('posted','cancelled')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, sales_order_id)
            REFERENCES operational_orders(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );
        CREATE TABLE sales_allocation_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          allocation_id uuid NOT NULL,
          order_line_id uuid NOT NULL,
          inventory_position_id uuid NOT NULL,
          reservation_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          uom text NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, allocation_id, order_line_id, inventory_position_id),
          UNIQUE (organization_id, reservation_id),
          FOREIGN KEY (organization_id, allocation_id)
            REFERENCES sales_allocations(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, order_line_id)
            REFERENCES operational_order_lines(organization_id, id),
          FOREIGN KEY (organization_id, inventory_position_id)
            REFERENCES inventory_positions(organization_id, id),
          FOREIGN KEY (organization_id, reservation_id)
            REFERENCES reservations(organization_id, id)
        );
        CREATE INDEX sales_allocations_order_idx
          ON sales_allocations (organization_id, sales_order_id, created_at);
        """
    )
    for table in ("sales_allocations", "sales_allocation_lines"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} {TENANT_POLICY}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sales_allocation_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS sales_allocations CASCADE")
