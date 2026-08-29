"""identity grants approvals jobs files and feature flags

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
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
        ALTER TABLE organizations
          ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
        ALTER TABLE users
          ADD COLUMN email_verified boolean NOT NULL DEFAULT false,
          ADD COLUMN disabled_at timestamptz;
        ALTER TABLE memberships
          ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version > 0);

        CREATE TABLE role_permissions (
          role text NOT NULL,
          permission text NOT NULL,
          PRIMARY KEY (role, permission)
        );
        INSERT INTO role_permissions (role, permission) VALUES
          ('owner', '*'),
          ('administrator', 'administration.manage'),
          ('administrator', 'inventory.view'),
          ('administrator', 'inventory.adjust'),
          ('administrator', 'exports.create'),
          ('planner', 'inventory.view'),
          ('planner', 'forecast.view'),
          ('planner', 'forecast.propose'),
          ('buyer', 'inventory.view'),
          ('buyer', 'purchasing.view'),
          ('buyer', 'purchasing.execute'),
          ('warehouse_operator', 'inventory.view'),
          ('warehouse_operator', 'inventory.adjust'),
          ('salesperson', 'inventory.view'),
          ('salesperson', 'orders.view'),
          ('salesperson', 'orders.execute'),
          ('accountant', 'inventory.view'),
          ('accountant', 'accounting.view'),
          ('accountant', 'exports.create'),
          ('viewer', 'inventory.view');

        CREATE TABLE warehouse_grants (
          organization_id uuid NOT NULL,
          user_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          granted_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, user_id, warehouse_id),
          FOREIGN KEY (organization_id, user_id)
            REFERENCES memberships(organization_id, user_id) ON DELETE CASCADE,
          FOREIGN KEY (organization_id, warehouse_id)
            REFERENCES warehouses(organization_id, id) ON DELETE CASCADE
        );

        CREATE TABLE invitations (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          email text NOT NULL,
          role text NOT NULL CHECK (role IN (
            'owner','administrator','planner','buyer','warehouse_operator',
            'salesperson','accountant','viewer'
          )),
          token_digest char(64) NOT NULL UNIQUE,
          invited_by uuid NOT NULL REFERENCES users(id),
          expires_at timestamptz NOT NULL,
          accepted_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          CHECK (expires_at > created_at)
        );
        CREATE UNIQUE INDEX invitations_active_email_idx
          ON invitations (organization_id, lower(email))
          WHERE accepted_at IS NULL AND revoked_at IS NULL;

        CREATE TABLE api_clients (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          name text NOT NULL,
          key_prefix text NOT NULL,
          secret_digest text NOT NULL,
          permissions text[] NOT NULL DEFAULT '{}',
          warehouse_ids uuid[] NOT NULL DEFAULT '{}',
          created_by uuid NOT NULL REFERENCES users(id),
          last_used_at timestamptz,
          expires_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, name),
          UNIQUE (key_prefix)
        );

        CREATE TABLE service_accounts (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          name text NOT NULL,
          subject text NOT NULL,
          permissions text[] NOT NULL DEFAULT '{}',
          warehouse_ids uuid[] NOT NULL DEFAULT '{}',
          disabled_at timestamptz,
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, name),
          UNIQUE (subject)
        );

        CREATE TABLE approval_policies (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          name text NOT NULL,
          action_type text NOT NULL,
          conditions jsonb NOT NULL DEFAULT '{}',
          minimum_approvals integer NOT NULL DEFAULT 1 CHECK (minimum_approvals > 0),
          approver_permissions text[] NOT NULL,
          active boolean NOT NULL DEFAULT true,
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_by uuid NOT NULL REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, name)
        );

        CREATE TABLE feature_flags (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          key text NOT NULL,
          enabled boolean NOT NULL,
          configuration jsonb NOT NULL DEFAULT '{}',
          updated_by uuid NOT NULL REFERENCES users(id),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, key)
        );

        CREATE TABLE jobs (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          queue text NOT NULL CHECK (queue IN (
            'imports','connectors','documents','forecasts','notifications','exports'
          )),
          job_type text NOT NULL,
          status text NOT NULL DEFAULT 'queued' CHECK (status IN (
            'queued','running','retrying','succeeded','failed','cancelled','dead_lettered'
          )),
          payload jsonb NOT NULL,
          result jsonb,
          correlation_id uuid NOT NULL,
          actor_id uuid,
          attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
          available_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          completed_at timestamptz,
          last_error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, id)
        );
        CREATE INDEX jobs_claim_idx ON jobs (queue, available_at, created_at)
          WHERE status IN ('queued','retrying');

        CREATE TABLE consumed_events (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          consumer text NOT NULL,
          event_id uuid NOT NULL,
          consumed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, consumer, event_id)
        );

        CREATE TABLE object_records (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          purpose text NOT NULL CHECK (purpose IN (
            'document','product_image','import','export','model_artifact','label'
          )),
          object_key text NOT NULL,
          original_name text NOT NULL,
          content_type text NOT NULL,
          content_length bigint NOT NULL CHECK (content_length >= 0),
          sha256 char(64),
          status text NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','quarantined','available','rejected','deleted'
          )),
          created_by uuid REFERENCES users(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          deleted_at timestamptz,
          PRIMARY KEY (organization_id, id),
          UNIQUE (organization_id, object_key),
          CHECK (object_key LIKE organization_id::text || '/%')
        );

        CREATE TABLE exports (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          export_type text NOT NULL,
          filters jsonb NOT NULL DEFAULT '{}',
          status text NOT NULL DEFAULT 'queued' CHECK (status IN (
            'queued','running','succeeded','failed','expired'
          )),
          requested_by uuid NOT NULL REFERENCES users(id),
          object_id uuid,
          correlation_id uuid NOT NULL,
          expires_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          PRIMARY KEY (organization_id, id),
          FOREIGN KEY (organization_id, object_id)
            REFERENCES object_records(organization_id, id)
        );

        ALTER TABLE outbox_events
          ADD COLUMN event_version integer NOT NULL DEFAULT 1 CHECK (event_version > 0),
          ADD COLUMN causation_id uuid,
          ADD COLUMN actor_id uuid,
          ADD COLUMN locked_at timestamptz,
          ADD COLUMN last_error text;
        """
    )

    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY organizations_tenant_isolation ON organizations
        USING (id = nullif(current_setting('app.organization_id', true), '')::uuid)
        WITH CHECK (id = nullif(current_setting('app.organization_id', true), '')::uuid)"""
    )
    for table in (
        "warehouse_grants",
        "invitations",
        "api_clients",
        "service_accounts",
        "approval_policies",
        "feature_flags",
        "jobs",
        "consumed_events",
        "object_records",
        "exports",
    ):
        _protect(table)


def downgrade() -> None:
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS last_error")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS locked_at")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS actor_id")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS causation_id")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS event_version")
    for table in (
        "exports",
        "object_records",
        "consumed_events",
        "jobs",
        "feature_flags",
        "approval_policies",
        "service_accounts",
        "api_clients",
        "invitations",
        "warehouse_grants",
        "role_permissions",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP POLICY IF EXISTS organizations_tenant_isolation ON organizations")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE memberships DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE memberships DROP COLUMN IF EXISTS created_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS disabled_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS version")
