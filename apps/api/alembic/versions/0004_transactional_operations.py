"""transactional orders and warehouse tasks

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_POLICY = """
USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
"""


def _protect(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} {TENANT_POLICY}")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
          ('administrator', 'purchasing.view'),
          ('administrator', 'purchasing.propose'),
          ('administrator', 'purchasing.approve'),
          ('administrator', 'purchasing.execute'),
          ('administrator', 'orders.view'),
          ('administrator', 'orders.propose'),
          ('administrator', 'orders.approve'),
          ('administrator', 'orders.execute'),
          ('buyer', 'purchasing.approve'),
          ('salesperson', 'orders.propose'),
          ('salesperson', 'orders.approve')
        ON CONFLICT DO NOTHING;

        CREATE TABLE operational_orders (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          kind text NOT NULL CHECK (kind IN ('purchase','sales')),
          order_number text NOT NULL,
          party_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL CHECK (state IN (
            'quote','draft','pending_approval','approved','sent','acknowledged',
            'partially_received','received','supplier_return','confirmed',
            'partially_allocated','allocated','backordered','dropship','picking',
            'partially_shipped','shipped','delivered','closed','cancelled'
          )),
          currency char(3) NOT NULL,
          expected_on date,
          notes text,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, kind, order_number),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );
        CREATE UNIQUE INDEX operational_orders_number_casefold_idx
          ON operational_orders (organization_id, kind, lower(order_number));
        CREATE INDEX operational_orders_queue_idx
          ON operational_orders (organization_id, kind, state, updated_at DESC);

        CREATE TABLE operational_order_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL,
          order_id uuid NOT NULL,
          line_number integer NOT NULL CHECK (line_number > 0),
          product_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          processed_quantity numeric(28,9) NOT NULL DEFAULT 0
            CHECK (processed_quantity >= 0 AND processed_quantity <= quantity),
          uom text NOT NULL,
          unit_price numeric(28,9) NOT NULL CHECK (unit_price >= 0),
          currency char(3) NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, order_id, line_number),
          FOREIGN KEY (organization_id, order_id)
            REFERENCES operational_orders(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id)
        );

        CREATE TABLE warehouse_tasks (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          task_number text NOT NULL,
          task_type text NOT NULL CHECK (task_type IN (
            'receive','putaway','pick','pack','transfer','count','replenish'
          )),
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'open' CHECK (state IN (
            'open','assigned','in_progress','completed','exception','cancelled'
          )),
          source_location_id uuid,
          destination_location_id uuid,
          product_id uuid,
          quantity numeric(28,9) CHECK (quantity IS NULL OR quantity > 0),
          uom text,
          reference_type text,
          reference_id uuid,
          assigned_to uuid REFERENCES users(id),
          priority integer NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 999),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, task_number),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, source_location_id)
            REFERENCES locations(organization_id, id),
          FOREIGN KEY (organization_id, destination_location_id)
            REFERENCES locations(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id)
        );
        CREATE UNIQUE INDEX warehouse_tasks_number_casefold_idx
          ON warehouse_tasks (organization_id, lower(task_number));
        CREATE INDEX warehouse_tasks_queue_idx
          ON warehouse_tasks (organization_id, warehouse_id, state, priority, created_at);
        """
    )
    for table in ("operational_orders", "operational_order_lines", "warehouse_tasks"):
        _protect(table)


def downgrade() -> None:
    for table in ("warehouse_tasks", "operational_order_lines", "operational_orders"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute(
        """
        DELETE FROM role_permissions WHERE permission IN (
          'purchasing.view','purchasing.propose','purchasing.approve','purchasing.execute',
          'orders.view','orders.propose','orders.approve','orders.execute'
        );
        """
    )
