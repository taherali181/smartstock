"""return authorizations and quarantine receipts

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TENANT_POLICY = """
USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE return_authorizations (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          return_number text NOT NULL,
          sales_order_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'requested' CHECK (state IN (
            'requested','authorized','received','inspected','refund','replacement','credit',
            'closed','rejected','cancelled'
          )),
          notes text,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, return_number),
          FOREIGN KEY (organization_id, sales_order_id)
            REFERENCES operational_orders(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );
        CREATE UNIQUE INDEX return_authorizations_number_casefold_idx
          ON return_authorizations (organization_id, lower(return_number));
        CREATE TABLE return_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          return_id uuid NOT NULL,
          order_line_id uuid NOT NULL,
          product_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          received_quantity numeric(28,9) NOT NULL DEFAULT 0
            CHECK (received_quantity >= 0 AND received_quantity <= quantity),
          uom text NOT NULL,
          reason_code text NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, return_id, order_line_id),
          FOREIGN KEY (organization_id, return_id)
            REFERENCES return_authorizations(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, order_line_id)
            REFERENCES operational_order_lines(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id)
        );
        CREATE TABLE return_receipts (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          return_id uuid NOT NULL,
          inventory_transaction_id uuid NOT NULL,
          received_by uuid NOT NULL REFERENCES users(id),
          received_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, return_id),
          FOREIGN KEY (organization_id, return_id)
            REFERENCES return_authorizations(organization_id, id),
          FOREIGN KEY (organization_id, inventory_transaction_id)
            REFERENCES inventory_transactions(organization_id, id)
        );
        """
    )
    for table in ("return_authorizations", "return_lines", "return_receipts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} {TENANT_POLICY}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS return_receipts CASCADE")
    op.execute("DROP TABLE IF EXISTS return_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS return_authorizations CASCADE")
