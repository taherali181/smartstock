"""prepare tracked inventory for a reversible downgrade

Revision ID: 0011
Revises: 0010

Revision 0003 introduced the lot and serial registries while the nullable
ledger reference columns already existed in 0001.  Reverting 0003 therefore
removes those registries.  Clear only those now-unrepresentable references
when 0011 is downgraded so a later upgrade can recreate the constraints.
Quantities, accounts, costs, business references, and ledger balance are not
changed.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Forward schema is unchanged.  This revision supplies the data transition
    # required before the historical lot/serial registries are removed.
    pass


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE inventory_ledger_lines
          DISABLE TRIGGER inventory_ledger_lines_immutable;

        UPDATE inventory_ledger_lines
           SET lot_id = NULL,
               serial_id = NULL
         WHERE lot_id IS NOT NULL OR serial_id IS NOT NULL;

        ALTER TABLE inventory_ledger_lines
          ENABLE TRIGGER inventory_ledger_lines_immutable;
        """
    )
