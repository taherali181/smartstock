# Domain contracts

## Inventory definitions

- `on_hand`: accepted physical inventory at a location.
- `reserved`: active reservations tied to specific inventory.
- `available`: sellable on-hand minus active reservations.
- `committed`: unfulfilled confirmed sales demand.
- `incoming`: unreceived approved purchase quantities plus inbound transfers.
- `in_transit`: shipped transfer quantities not yet received.
- `backordered`: confirmed demand not currently allocatable.
- `ATP(horizon)`: available plus eligible incoming before the horizon, less unreserved committed demand and policy safety stock.

Quantities are `NUMERIC(28,9)` plus UOM. Money is `NUMERIC` plus ISO 4217 currency. Floating-point money and integer-only quantities are forbidden. Conversion factors are decimal, versioned, and reversible.

## State machines

```text
PO: draft → pending_approval → approved → sent → acknowledged
    → partially_received → received → closed

Sales: quote → draft → confirmed → partially_allocated/allocated
       → picking → partially_shipped/shipped → delivered → closed

Transfer: draft → approved → picking → shipped
          → partially_received/received → discrepancy_review → closed

Count: scheduled → frozen → counting → review → approved/posted

RMA: requested → authorized → received → inspected
     → refund/replacement/credit → closed

Shipment: planned → picking → packed → labelled → shipped → delivered

AI proposal: draft → validating → awaiting_review → approved/rejected/expired
             → executing → succeeded/failed
```

Cancellation, backorder, dropship, recount, exception, void, and supplier-return branches are explicit commands with their own authorization and invariants. `PATCH status` is never an allowed transition mechanism.

## Ledger conservation

Every inventory transaction has two or more lines and `Σ quantity = 0` for the transaction. Physical stock uses `on_hand`; transfers use source, `in_transit`, discrepancy, and destination accounts; receipts and shipments balance against `external`. Reservations are separately versioned claims and do not create physical quantity.

The valuation method is organization-selectable weighted average or FIFO. It becomes immutable after the first financial posting unless a controlled, audited revaluation migration succeeds.

## Command envelope

All consequential commands include organization, actor, reason, business reference, correlation ID, tenant-scoped idempotency key, and expected entity versions. Approval commands additionally include the exact source versions used by the proposal and revalidate authorization, inventory, price, and policy at approval time.
