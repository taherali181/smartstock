"""catalog, reservations, transfers, counts and valuation

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
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
          ('administrator', 'catalog.view'),
          ('administrator', 'catalog.manage'),
          ('administrator', 'warehouse.manage'),
          ('planner', 'catalog.view'),
          ('buyer', 'catalog.view'),
          ('warehouse_operator', 'catalog.view'),
          ('salesperson', 'catalog.view'),
          ('accountant', 'catalog.view'),
          ('viewer', 'catalog.view')
        ON CONFLICT DO NOTHING;

        ALTER TABLE products
          ADD COLUMN description text,
          ADD COLUMN category_id uuid,
          ADD COLUMN brand_id uuid,
          ADD COLUMN tracking_mode text NOT NULL DEFAULT 'none'
            CHECK (tracking_mode IN ('none','lot','serial')),
          ADD COLUMN weight numeric(28,9) CHECK (weight IS NULL OR weight >= 0),
          ADD COLUMN weight_uom text,
          ADD COLUMN custom_fields jsonb NOT NULL DEFAULT '{}';

        ALTER TABLE warehouses
          ADD COLUMN active boolean NOT NULL DEFAULT true,
          ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

        ALTER TABLE locations
          ADD COLUMN zone_id uuid,
          ADD COLUMN active boolean NOT NULL DEFAULT true,
          ADD COLUMN pick_sequence integer NOT NULL DEFAULT 0 CHECK (pick_sequence >= 0),
          ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

        ALTER TABLE inventory_positions
          ADD COLUMN average_unit_cost numeric(28,9) NOT NULL DEFAULT 0
            CHECK (average_unit_cost >= 0),
          ADD COLUMN inventory_value numeric(28,9) NOT NULL DEFAULT 0,
          ADD CONSTRAINT serial_position_quantity_check CHECK (
            serial_key = '00000000-0000-0000-0000-000000000000'::uuid OR
            (on_hand >= 0 AND on_hand <= 1 AND reserved >= 0 AND reserved <= 1)
          );

        CREATE UNIQUE INDEX products_sku_casefold_idx
          ON products (organization_id, lower(sku));
        CREATE UNIQUE INDEX warehouses_code_casefold_idx
          ON warehouses (organization_id, lower(code));
        CREATE UNIQUE INDEX locations_code_casefold_idx
          ON locations (organization_id, warehouse_id, lower(code));

        CREATE TABLE categories (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          parent_id uuid,
          name text NOT NULL,
          slug text NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, slug),
          FOREIGN KEY (organization_id, parent_id)
            REFERENCES categories(organization_id, id)
        );

        CREATE TABLE brands (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          name text NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, name)
        );

        ALTER TABLE products
          ADD CONSTRAINT products_category_fk FOREIGN KEY (organization_id, category_id)
            REFERENCES categories(organization_id, id),
          ADD CONSTRAINT products_brand_fk FOREIGN KEY (organization_id, brand_id)
            REFERENCES brands(organization_id, id);

        CREATE TABLE product_variants (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          sku text NOT NULL,
          name text NOT NULL,
          attributes jsonb NOT NULL DEFAULT '{}',
          lifecycle_state text NOT NULL DEFAULT 'active'
            CHECK (lifecycle_state IN ('draft','active','discontinued','archived')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, sku),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX product_variants_sku_casefold_idx
          ON product_variants (organization_id, lower(sku));

        CREATE TABLE product_barcodes (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          variant_id uuid,
          barcode text NOT NULL,
          barcode_type text NOT NULL DEFAULT 'custom',
          is_primary boolean NOT NULL DEFAULT false,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, barcode),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, variant_id)
            REFERENCES product_variants(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE uom_conversions (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          from_uom text NOT NULL,
          to_uom text NOT NULL,
          factor numeric(28,9) NOT NULL CHECK (factor > 0),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, product_id, from_uom, to_uom),
          CHECK (from_uom <> to_uom),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE suppliers (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          code text NOT NULL,
          name text NOT NULL,
          currency char(3) NOT NULL,
          email text,
          active boolean NOT NULL DEFAULT true,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          custom_fields jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, code)
        );
        CREATE UNIQUE INDEX suppliers_code_casefold_idx
          ON suppliers (organization_id, lower(code));

        CREATE TABLE product_suppliers (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          supplier_id uuid NOT NULL,
          supplier_sku text,
          purchase_uom text NOT NULL,
          minimum_order_quantity numeric(28,9) NOT NULL DEFAULT 0
            CHECK (minimum_order_quantity >= 0),
          case_pack numeric(28,9) NOT NULL DEFAULT 1 CHECK (case_pack > 0),
          lead_time_days integer CHECK (lead_time_days IS NULL OR lead_time_days >= 0),
          preferred boolean NOT NULL DEFAULT false,
          last_unit_cost numeric(28,9) CHECK (last_unit_cost IS NULL OR last_unit_cost >= 0),
          currency char(3) NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, product_id, supplier_id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, supplier_id)
            REFERENCES suppliers(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE supplier_price_breaks (
          organization_id uuid NOT NULL,
          product_supplier_id uuid NOT NULL,
          minimum_quantity numeric(28,9) NOT NULL CHECK (minimum_quantity > 0),
          unit_price numeric(28,9) NOT NULL CHECK (unit_price >= 0),
          PRIMARY KEY (organization_id, product_supplier_id, minimum_quantity),
          FOREIGN KEY (organization_id, product_supplier_id)
            REFERENCES product_suppliers(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE customers (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          code text NOT NULL,
          name text NOT NULL,
          email text,
          currency char(3) NOT NULL,
          active boolean NOT NULL DEFAULT true,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          custom_fields jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, code)
        );
        CREATE UNIQUE INDEX customers_code_casefold_idx
          ON customers (organization_id, lower(code));

        CREATE TABLE kits (
          organization_id uuid NOT NULL,
          product_id uuid NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          active boolean NOT NULL DEFAULT true,
          PRIMARY KEY (organization_id, product_id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE kit_components (
          organization_id uuid NOT NULL,
          kit_product_id uuid NOT NULL,
          component_product_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          uom text NOT NULL,
          PRIMARY KEY (organization_id, kit_product_id, component_product_id),
          CHECK (kit_product_id <> component_product_id),
          FOREIGN KEY (organization_id, kit_product_id)
            REFERENCES kits(organization_id, product_id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, component_product_id)
            REFERENCES products(organization_id, id)
        );

        CREATE TABLE warehouse_zones (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          warehouse_id uuid NOT NULL,
          code text NOT NULL,
          name text NOT NULL,
          zone_type text NOT NULL DEFAULT 'storage'
            CHECK (zone_type IN ('receiving','storage','picking','packing','shipping','quarantine')),
          active boolean NOT NULL DEFAULT true,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, warehouse_id, id),
          UNIQUE (organization_id, warehouse_id, code),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id) ON DELETE CASCADE
        );

        ALTER TABLE locations
          ADD CONSTRAINT locations_zone_fk FOREIGN KEY (organization_id, warehouse_id, zone_id)
            REFERENCES warehouse_zones(organization_id, warehouse_id, id);

        CREATE TABLE lots (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          lot_number text NOT NULL,
          manufactured_on date,
          expires_on date,
          status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','quarantined','expired','recalled','closed')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, id, product_id),
          UNIQUE (organization_id, product_id, lot_number),
          CHECK (expires_on IS NULL OR manufactured_on IS NULL OR expires_on >= manufactured_on),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id)
        );

        CREATE TABLE serial_numbers (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          serial_number text NOT NULL,
          status text NOT NULL DEFAULT 'available'
            CHECK (status IN ('available','reserved','shipped','quarantined','damaged','retired')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, id, product_id),
          UNIQUE (organization_id, serial_number),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id)
        );

        ALTER TABLE inventory_ledger_lines
          ADD CONSTRAINT inventory_ledger_lot_fk FOREIGN KEY (organization_id, lot_id, product_id)
            REFERENCES lots(organization_id, id, product_id),
          ADD CONSTRAINT inventory_ledger_serial_fk FOREIGN KEY (organization_id, serial_id, product_id)
            REFERENCES serial_numbers(organization_id, id, product_id);

        CREATE TABLE reservations (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          inventory_position_id uuid NOT NULL,
          source_type text NOT NULL,
          source_id uuid NOT NULL,
          quantity numeric(28,9) NOT NULL CHECK (quantity > 0),
          status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','released','consumed','expired')),
          idempotency_key text NOT NULL,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          expires_at timestamptz,
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, idempotency_key),
          FOREIGN KEY (organization_id, inventory_position_id)
            REFERENCES inventory_positions(organization_id, id)
        );
        CREATE UNIQUE INDEX reservations_active_source_position_idx
          ON reservations (organization_id, source_type, source_id, inventory_position_id)
          WHERE status = 'active';

        CREATE TABLE transfers (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          transfer_number text NOT NULL,
          source_warehouse_id uuid NOT NULL,
          destination_warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'draft' CHECK (state IN (
            'draft','approved','picking','shipped','partially_received','received',
            'discrepancy_review','closed','cancelled'
          )),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          approved_by uuid REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, transfer_number),
          CHECK (source_warehouse_id <> destination_warehouse_id),
          FOREIGN KEY (organization_id, source_warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, destination_warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );

        CREATE TABLE transfer_lines (
          organization_id uuid NOT NULL,
          transfer_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          source_location_id uuid NOT NULL,
          destination_location_id uuid NOT NULL,
          lot_id uuid,
          serial_id uuid,
          uom text NOT NULL,
          requested_quantity numeric(28,9) NOT NULL CHECK (requested_quantity > 0),
          shipped_quantity numeric(28,9) NOT NULL DEFAULT 0 CHECK (shipped_quantity >= 0),
          received_quantity numeric(28,9) NOT NULL DEFAULT 0 CHECK (received_quantity >= 0),
          discrepancy_quantity numeric(28,9) NOT NULL DEFAULT 0,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, transfer_id)
            REFERENCES transfers(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, source_location_id)
            REFERENCES locations(organization_id, id),
          FOREIGN KEY (organization_id, destination_location_id)
            REFERENCES locations(organization_id, id),
          FOREIGN KEY (organization_id, lot_id)
            REFERENCES lots(organization_id, id),
          FOREIGN KEY (organization_id, serial_id)
            REFERENCES serial_numbers(organization_id, id),
          CHECK (shipped_quantity <= requested_quantity),
          CHECK (received_quantity <= shipped_quantity)
        );

        CREATE TABLE cycle_counts (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          count_number text NOT NULL,
          warehouse_id uuid NOT NULL,
          state text NOT NULL DEFAULT 'scheduled' CHECK (state IN (
            'scheduled','frozen','counting','review','approved','posted','recount','cancelled'
          )),
          blind_count boolean NOT NULL DEFAULT true,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          approved_by uuid REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, count_number),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );

        CREATE TABLE cycle_count_lines (
          organization_id uuid NOT NULL,
          cycle_count_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          inventory_position_id uuid NOT NULL,
          snapshot_quantity numeric(28,9) NOT NULL,
          counted_quantity numeric(28,9),
          variance_quantity numeric(28,9),
          counted_by uuid REFERENCES users(id),
          counted_at timestamptz,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, cycle_count_id, inventory_position_id),
          FOREIGN KEY (organization_id, cycle_count_id)
            REFERENCES cycle_counts(organization_id, id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, inventory_position_id)
            REFERENCES inventory_positions(organization_id, id)
        );

        CREATE TABLE cost_layers (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          product_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          source_transaction_id uuid NOT NULL,
          received_at timestamptz NOT NULL,
          original_quantity numeric(28,9) NOT NULL CHECK (original_quantity > 0),
          remaining_quantity numeric(28,9) NOT NULL CHECK (remaining_quantity >= 0),
          unit_cost numeric(28,9) NOT NULL CHECK (unit_cost >= 0),
          currency char(3) NOT NULL,
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id),
          FOREIGN KEY (organization_id, source_transaction_id)
            REFERENCES inventory_transactions(organization_id, id),
          CHECK (remaining_quantity <= original_quantity)
        );
        CREATE INDEX cost_layers_fifo_idx
          ON cost_layers (organization_id, product_id, warehouse_id, received_at, id)
          WHERE remaining_quantity > 0;

        CREATE TABLE valuation_postings (
          organization_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          inventory_transaction_id uuid NOT NULL,
          product_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          valuation_method text NOT NULL CHECK (valuation_method IN ('weighted_average','fifo')),
          quantity numeric(28,9) NOT NULL CHECK (quantity <> 0),
          unit_cost numeric(28,9) NOT NULL CHECK (unit_cost >= 0),
          total_cost numeric(28,9) NOT NULL,
          currency char(3) NOT NULL,
          posted_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, inventory_transaction_id)
            REFERENCES inventory_transactions(organization_id, id),
          FOREIGN KEY (organization_id, product_id)
            REFERENCES products(organization_id, id),
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id)
        );

        CREATE TABLE import_runs (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          import_type text NOT NULL,
          source_hash char(64) NOT NULL,
          status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','validating','running','succeeded','failed')),
          row_counts jsonb NOT NULL DEFAULT '{}',
          reconciliation jsonb,
          error_report jsonb,
          created_by uuid NOT NULL REFERENCES users(id),
          correlation_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, import_type, source_hash)
        );

        CREATE TABLE import_id_mappings (
          organization_id uuid NOT NULL,
          import_run_id uuid NOT NULL,
          resource_type text NOT NULL,
          legacy_id text NOT NULL,
          smartstock_id uuid NOT NULL,
          PRIMARY KEY (organization_id, import_run_id, resource_type, legacy_id),
          FOREIGN KEY (organization_id, import_run_id)
            REFERENCES import_runs(organization_id, id) ON DELETE CASCADE
        );

        CREATE VIEW inventory_reconciliation WITH (security_invoker = true) AS
        SELECT p.organization_id, p.id AS inventory_position_id,
               p.on_hand AS projected_on_hand,
               COALESCE(sum(l.quantity) FILTER (WHERE l.account = 'on_hand'), 0) AS ledger_on_hand,
               p.reserved AS projected_reserved,
               COALESCE((
                 SELECT sum(r.quantity) FROM reservations r
                 WHERE r.organization_id = p.organization_id
                   AND r.inventory_position_id = p.id AND r.status = 'active'
               ), 0) AS reservation_total
        FROM inventory_positions p
        LEFT JOIN inventory_ledger_lines l
          ON l.organization_id = p.organization_id
         AND l.product_id = p.product_id
         AND l.warehouse_id = p.warehouse_id
         AND l.location_id = p.location_id
         AND l.condition = p.condition
         AND l.ownership = p.ownership
         AND COALESCE(l.lot_id, '00000000-0000-0000-0000-000000000000'::uuid) = p.lot_key
         AND COALESCE(l.serial_id, '00000000-0000-0000-0000-000000000000'::uuid) = p.serial_key
         AND l.uom = p.uom
        GROUP BY p.organization_id, p.id, p.on_hand, p.reserved;
        """
    )

    for table in (
        "categories", "brands", "product_variants", "product_barcodes", "uom_conversions",
        "suppliers", "product_suppliers", "supplier_price_breaks", "customers", "kits",
        "kit_components", "warehouse_zones", "lots", "serial_numbers", "reservations",
        "transfers", "transfer_lines", "cycle_counts", "cycle_count_lines", "cost_layers",
        "valuation_postings", "import_runs", "import_id_mappings",
    ):
        _protect(table)

    op.execute(
        """
        CREATE TRIGGER valuation_postings_immutable
          BEFORE UPDATE OR DELETE ON valuation_postings
          FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

        CREATE FUNCTION protect_valuation_method() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.valuation_method <> OLD.valuation_method AND EXISTS (
            SELECT 1 FROM valuation_postings WHERE organization_id = OLD.id
          ) THEN
            RAISE EXCEPTION 'valuation method cannot change after financial postings';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER organizations_valuation_method_immutable
          BEFORE UPDATE OF valuation_method ON organizations
          FOR EACH ROW EXECUTE FUNCTION protect_valuation_method();
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission IN ('catalog.view','catalog.manage','warehouse.manage')")
    op.execute("DROP INDEX IF EXISTS locations_code_casefold_idx")
    op.execute("DROP INDEX IF EXISTS warehouses_code_casefold_idx")
    op.execute("DROP INDEX IF EXISTS products_sku_casefold_idx")
    op.execute("DROP TRIGGER IF EXISTS organizations_valuation_method_immutable ON organizations")
    op.execute("DROP FUNCTION IF EXISTS protect_valuation_method()")
    op.execute("DROP VIEW IF EXISTS inventory_reconciliation")
    for table in (
        "import_id_mappings", "import_runs", "valuation_postings", "cost_layers",
        "cycle_count_lines", "cycle_counts", "transfer_lines", "transfers", "reservations",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE inventory_ledger_lines DROP CONSTRAINT IF EXISTS inventory_ledger_serial_fk")
    op.execute("ALTER TABLE inventory_ledger_lines DROP CONSTRAINT IF EXISTS inventory_ledger_lot_fk")
    for table in (
        "serial_numbers", "lots", "kit_components", "kits", "supplier_price_breaks",
        "product_suppliers", "suppliers", "customers", "uom_conversions", "product_barcodes",
        "product_variants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE locations DROP CONSTRAINT IF EXISTS locations_zone_fk")
    op.execute("DROP TABLE IF EXISTS warehouse_zones CASCADE")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_brand_fk")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_category_fk")
    op.execute("DROP TABLE IF EXISTS brands CASCADE")
    op.execute("DROP TABLE IF EXISTS categories CASCADE")
    op.execute("ALTER TABLE inventory_positions DROP CONSTRAINT IF EXISTS serial_position_quantity_check")
    op.execute("ALTER TABLE inventory_positions DROP COLUMN IF EXISTS inventory_value")
    op.execute("ALTER TABLE inventory_positions DROP COLUMN IF EXISTS average_unit_cost")
    for column in ("updated_at", "created_at", "version", "pick_sequence", "active", "zone_id"):
        op.execute(f"ALTER TABLE locations DROP COLUMN IF EXISTS {column}")
    for column in ("updated_at", "created_at", "version", "active"):
        op.execute(f"ALTER TABLE warehouses DROP COLUMN IF EXISTS {column}")
    for column in ("custom_fields", "weight_uom", "weight", "tracking_mode", "brand_id", "category_id", "description"):
        op.execute(f"ALTER TABLE products DROP COLUMN IF EXISTS {column}")
