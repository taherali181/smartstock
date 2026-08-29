"""platform tenancy and inventory truth

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE organizations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug text NOT NULL UNIQUE,
          name text NOT NULL,
          valuation_method text NOT NULL DEFAULT 'weighted_average'
            CHECK (valuation_method IN ('weighted_average', 'fifo')),
          currency char(3) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE users (
          id uuid PRIMARY KEY,
          email text NOT NULL UNIQUE,
          display_name text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE memberships (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          user_id uuid NOT NULL REFERENCES users(id),
          role text NOT NULL CHECK (role IN (
            'owner','administrator','planner','buyer','warehouse_operator',
            'salesperson','accountant','viewer'
          )),
          active boolean NOT NULL DEFAULT true,
          PRIMARY KEY (organization_id, user_id)
        );

        CREATE TABLE warehouses (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          code text NOT NULL,
          name text NOT NULL,
          timezone text NOT NULL,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, code)
        );

        CREATE TABLE locations (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          warehouse_id uuid NOT NULL,
          code text NOT NULL,
          location_type text NOT NULL CHECK (location_type IN (
            'bin','receiving','shipping','in_transit','discrepancy'
          )),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, warehouse_id, code),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );

        CREATE TABLE products (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          sku text NOT NULL,
          name text NOT NULL,
          base_uom text NOT NULL,
          lifecycle_state text NOT NULL DEFAULT 'active'
            CHECK (lifecycle_state IN ('draft','active','discontinued','archived')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, sku)
        );

        CREATE TABLE inventory_transactions (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          actor_id uuid NOT NULL REFERENCES users(id),
          reason_code text NOT NULL,
          business_reference text NOT NULL,
          idempotency_key text NOT NULL,
          correlation_id uuid NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, idempotency_key)
        );

        CREATE TABLE inventory_ledger_lines (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          transaction_id uuid NOT NULL,
          line_number integer NOT NULL CHECK (line_number > 0),
          account text NOT NULL CHECK (account IN ('on_hand','in_transit','external','discrepancy')),
          product_id uuid,
          warehouse_id uuid,
          location_id uuid,
          condition text CHECK (condition IN ('sellable','quarantined','damaged','expired')),
          ownership text,
          lot_id uuid,
          serial_id uuid,
          quantity numeric(28,9) NOT NULL CHECK (quantity <> 0),
          uom text,
          unit_cost numeric(28,9),
          currency char(3),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, transaction_id, line_number),
          FOREIGN KEY (organization_id, transaction_id)
            REFERENCES inventory_transactions(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, location_id)
            REFERENCES locations(organization_id, id),
          CHECK (
            (account IN ('external','discrepancy')) OR
            (product_id IS NOT NULL AND warehouse_id IS NOT NULL AND location_id IS NOT NULL
             AND condition IS NOT NULL AND ownership IS NOT NULL AND uom IS NOT NULL)
          )
        );

        CREATE TABLE inventory_positions (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          location_id uuid NOT NULL,
          condition text NOT NULL CHECK (condition IN ('sellable','quarantined','damaged','expired')),
          ownership text NOT NULL,
          lot_key uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
          serial_key uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
          uom text NOT NULL,
          on_hand numeric(28,9) NOT NULL DEFAULT 0,
          reserved numeric(28,9) NOT NULL DEFAULT 0 CHECK (reserved >= 0),
          version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (
            organization_id, product_id, warehouse_id, location_id, condition,
            ownership, lot_key, serial_key, uom
          ),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, location_id)
            REFERENCES locations(organization_id, id)
        );

        CREATE TABLE idempotency_records (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          key text NOT NULL,
          request_hash char(64) NOT NULL,
          response_status integer NOT NULL,
          response_body jsonb NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (organization_id, key)
        );

        CREATE TABLE outbox_events (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          topic text NOT NULL,
          aggregate_id uuid NOT NULL,
          correlation_id uuid NOT NULL,
          payload jsonb NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz,
          attempts integer NOT NULL DEFAULT 0,
          PRIMARY KEY (organization_id, id)
        );
        CREATE INDEX outbox_unpublished_idx ON outbox_events (occurred_at)
          WHERE published_at IS NULL;

        CREATE TABLE audit_events (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          actor_id uuid,
          action text NOT NULL,
          resource_type text NOT NULL,
          resource_id text NOT NULL,
          correlation_id uuid NOT NULL,
          before_state jsonb,
          after_state jsonb,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id)
        );

        CREATE TABLE action_proposals (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          proposal_type text NOT NULL,
          state text NOT NULL CHECK (state IN (
            'draft','validating','awaiting_review','approved','rejected','expired',
            'executing','succeeded','failed'
          )),
          command_payload jsonb NOT NULL,
          source_versions jsonb NOT NULL,
          impact_preview jsonb NOT NULL,
          created_by uuid NOT NULL REFERENCES users(id),
          reviewed_by uuid REFERENCES users(id),
          version bigint NOT NULL DEFAULT 1,
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id)
        );
        """
    )

    tenant_tables = (
        "memberships",
        "warehouses",
        "locations",
        "products",
        "inventory_transactions",
        "inventory_ledger_lines",
        "inventory_positions",
        "idempotency_records",
        "outbox_events",
        "audit_events",
        "action_proposals",
    )
    for table in tenant_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY {table}_tenant_isolation ON {table}
            USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)
            WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)"""
        )

    op.execute(
        """
        CREATE FUNCTION reject_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'inventory ledger rows are immutable';
        END $$;
        CREATE TRIGGER inventory_transactions_immutable
          BEFORE UPDATE OR DELETE ON inventory_transactions
          FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
        CREATE TRIGGER inventory_ledger_lines_immutable
          BEFORE UPDATE OR DELETE ON inventory_ledger_lines
          FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
        CREATE TRIGGER audit_events_immutable
          BEFORE UPDATE OR DELETE ON audit_events
          FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

        CREATE CONSTRAINT TRIGGER inventory_transaction_balanced
          AFTER INSERT ON inventory_ledger_lines
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION reject_unbalanced_inventory_transaction();
        """.replace(
            "CREATE CONSTRAINT TRIGGER inventory_transaction_balanced\n"
            "          AFTER INSERT ON inventory_ledger_lines\n"
            "          DEFERRABLE INITIALLY DEFERRED\n"
            "          FOR EACH ROW EXECUTE FUNCTION reject_unbalanced_inventory_transaction();",
            """CREATE FUNCTION reject_unbalanced_inventory_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM inventory_ledger_lines
                WHERE organization_id = NEW.organization_id AND transaction_id = NEW.transaction_id
                GROUP BY organization_id, transaction_id HAVING sum(quantity) <> 0
              ) THEN
                RAISE EXCEPTION 'inventory transaction % is unbalanced', NEW.transaction_id;
              END IF;
              RETURN NULL;
            END $$;
            CREATE CONSTRAINT TRIGGER inventory_transaction_balanced
              AFTER INSERT ON inventory_ledger_lines
              DEFERRABLE INITIALLY DEFERRED
              FOR EACH ROW EXECUTE FUNCTION reject_unbalanced_inventory_transaction();""",
        )
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS action_proposals CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP TABLE IF EXISTS outbox_events CASCADE")
    op.execute("DROP TABLE IF EXISTS idempotency_records CASCADE")
    op.execute("DROP TABLE IF EXISTS inventory_positions CASCADE")
    op.execute("DROP TABLE IF EXISTS inventory_ledger_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS inventory_transactions CASCADE")
    op.execute("DROP TABLE IF EXISTS products CASCADE")
    op.execute("DROP TABLE IF EXISTS locations CASCADE")
    op.execute("DROP TABLE IF EXISTS warehouses CASCADE")
    op.execute("DROP TABLE IF EXISTS memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_unbalanced_inventory_transaction()")
    op.execute("DROP FUNCTION IF EXISTS reject_ledger_mutation()")
