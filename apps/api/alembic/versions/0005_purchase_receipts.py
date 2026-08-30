"""purchase receipts and atomic inventory posting

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_POLICY = """
USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
"""


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
          ('administrator', 'warehouse.execute'),
          ('warehouse_operator', 'warehouse.execute')
        ON CONFLICT DO NOTHING;

        ALTER TABLE operational_order_lines
          DROP CONSTRAINT operational_order_lines_processed_quantity_check;
        ALTER TABLE operational_order_lines
          ADD CONSTRAINT operational_order_lines_processed_nonnegative
          CHECK (processed_quantity >= 0);
        """
    )
    op.execute(
        """
        CREATE TABLE receipts (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          receipt_number text NOT NULL,
          purchase_order_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'posted' CHECK (state IN ('posted','voided')),
          inventory_transaction_id uuid NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          posted_by uuid NOT NULL REFERENCES users(id),
          posted_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, receipt_number),
          FOREIGN KEY (organization_id, purchase_order_id)
            REFERENCES operational_orders(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, inventory_transaction_id)
            REFERENCES inventory_transactions(organization_id, id)
        );
        CREATE TABLE receipt_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          receipt_id uuid NOT NULL,
          order_line_id uuid NOT NULL,
          product_id uuid NOT NULL,
          location_id uuid NOT NULL,
          accepted_quantity numeric(28,9) NOT NULL DEFAULT 0 CHECK (accepted_quantity >= 0),
          rejected_quantity numeric(28,9) NOT NULL DEFAULT 0 CHECK (rejected_quantity >= 0),
          uom text NOT NULL,
          unit_cost numeric(28,9) NOT NULL CHECK (unit_cost >= 0),
          currency char(3) NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, receipt_id, order_line_id),
          CHECK (accepted_quantity + rejected_quantity > 0),
          FOREIGN KEY (organization_id, receipt_id)
            REFERENCES receipts(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, order_line_id)
            REFERENCES operational_order_lines(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, location_id)
            REFERENCES locations(organization_id, id)
        );
        CREATE UNIQUE INDEX receipts_number_casefold_idx
          ON receipts (organization_id, lower(receipt_number));
        """
    )
    for table in ("receipts", "receipt_lines"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} {TENANT_POLICY}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS receipt_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS receipts CASCADE")
    op.execute(
        """
        ALTER TABLE operational_order_lines
          DROP CONSTRAINT operational_order_lines_processed_nonnegative;
        UPDATE operational_order_lines
          SET processed_quantity = quantity
          WHERE processed_quantity > quantity;
        ALTER TABLE operational_order_lines
          ADD CONSTRAINT operational_order_lines_processed_quantity_check
          CHECK (processed_quantity >= 0 AND processed_quantity <= quantity);
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission = 'warehouse.execute'
          AND role IN ('administrator', 'warehouse_operator');
        """
    )
