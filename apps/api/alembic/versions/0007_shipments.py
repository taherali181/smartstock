"""reservation-backed shipment execution

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TENANT_POLICY = """
USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE shipments (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          sales_order_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'shipped' CHECK (state IN ('shipped','delivered','voided')),
          inventory_transaction_id uuid NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          shipped_by uuid NOT NULL REFERENCES users(id),
          shipped_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, sales_order_id)
            REFERENCES operational_orders(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, inventory_transaction_id)
            REFERENCES inventory_transactions(organization_id, id)
        );
        CREATE TABLE shipment_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          shipment_id uuid NOT NULL,
          order_line_id uuid NOT NULL,
          reservation_id uuid NOT NULL,
          inventory_position_id uuid NOT NULL,
          product_id uuid NOT NULL,
          location_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          uom text NOT NULL,
          unit_cost numeric(28,9) NOT NULL CHECK (unit_cost >= 0),
          currency char(3) NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, shipment_id, reservation_id),
          FOREIGN KEY (organization_id, shipment_id)
            REFERENCES shipments(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, order_line_id)
            REFERENCES operational_order_lines(organization_id, id),
          FOREIGN KEY (organization_id, reservation_id)
            REFERENCES reservations(organization_id, id),
          FOREIGN KEY (organization_id, inventory_position_id)
            REFERENCES inventory_positions(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, location_id)
            REFERENCES locations(organization_id, id)
        );
        CREATE INDEX shipments_order_idx
          ON shipments (organization_id, sales_order_id, shipped_at);
        """
    )
    for table in ("shipments", "shipment_lines"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} {TENANT_POLICY}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shipment_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS shipments CASCADE")
