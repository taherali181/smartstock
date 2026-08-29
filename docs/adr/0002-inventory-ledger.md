# ADR 0002: inventory ledger and projections

Status: accepted

Inventory truth is an immutable transaction header plus balanced decimal ledger lines. Positions and valuation layers are rebuildable projections. Commands are idempotent, version-checked, actor/reason/reference-bound, negative-stock-policy checked under database locks, and coupled to audit/outbox writes in one commit.

This makes retries, transfers, reconciliation, valuation, and historical audit explicit. Direct updates to stock totals and mutation/deletion of ledger history are prohibited.
