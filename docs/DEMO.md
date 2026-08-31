# SmartStock demo data

Run the deterministic seed against the local PostgreSQL database:

```bash
SMARTSTOCK_DATABASE_URL="postgresql+psycopg://smartstock:smartstock@127.0.0.1:5432/smartstock" \
PYTHONPATH=apps/api python3 -m smartstock_api.seed
```

The command is safe to run repeatedly. It uses stable UUIDs and idempotency keys and does not duplicate inventory, ledger, order, or task records.

## Development identity

- Organization ID: `00000000-0000-0000-0000-000000000001`
- User ID: `00000000-0000-0000-0000-000000000001`
- Organization: SmartStock Demo Company (`smartstock-demo`)
- User: `demo@smartstock.local`, owner role

Development requests use the identity headers:

```text
X-Development-Organization: 00000000-0000-0000-0000-000000000001
X-Development-User: 00000000-0000-0000-0000-000000000001
```

## Stable demo records

- Warehouses: `WH-MAIN`, `WH-EAST`, `WH-WEST`
- Each warehouse has receiving, storage, picking, and packing zones plus `RECEIVING`, `A-01`, `B-01`, `PICK-01`, and `SHIPPING` locations.
- Products: `SKU-1001` through `SKU-1040`
- Lot-tracked: `SKU-1033` through `SKU-1036`
- Serial-tracked: `SKU-1037` through `SKU-1040`
- Suppliers: `ACME`, `NORTHSTAR`, `MAPLE`, `PACIFIC`, `SUMMIT`, `HARBOR`
- Customers: `CUST-001` through `CUST-008`
- Purchase orders: `PO-2001` is acknowledged and has receiving work; `PO-2002` is approved.
- Sales orders: `SO-1001` is a quote, `SO-1002` is allocated, and `SO-1004` is confirmed but intentionally exceeds available `SKU-1017` stock in `WH-MAIN`.
- Warehouse tasks: `RCV-PO-2001`, `PICK-SO-1002`, `COUNT-WH-MAIN-001`, and `XFER-WH-MAIN-EAST-001`.

`SKU-1017` is the low-stock demonstration item. `WH-MAIN` starts with 12 units, its reorder point is 40, and `PO-2001` has 200 incoming units from Acme Supply Co.

## Canvas demo questions

1. `how much SKU-1017 do we have in WH-MAIN?`
2. `what is below reorder point?`
3. `why can't I allocate sales order SO-1004?`
4. `what did we receive today?`
5. `raise a PO for 200 of SKU-1017 from Acme`

The fifth request must create an inert action proposal with an impact preview. It must not execute until an authorized user approves it.
