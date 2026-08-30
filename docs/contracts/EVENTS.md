# Event catalog

Events are written to the transactional outbox and delivered at least once. Envelopes include event ID, event type/version, organization ID, aggregate ID/version, correlation/causation IDs, actor, occurred-at time, and payload. Consumers claim a stable consumer name plus event ID before applying effects.

| Event | Owning aggregate | Primary consumers |
| --- | --- | --- |
| `inventory.ledger_posted.v1` | inventory transaction | projections, valuation, audit, search |
| `inventory.position_changed.v1` | inventory position | ATP, connector availability, exceptions |
| `order.confirmed.v1` | sales order | allocation, forecasting facts |
| `order.allocation_changed.v1` | allocation | warehouse tasks, ATP |
| `shipment.shipped.v1` | shipment | orders, connectors, accounting |
| `purchase_order.approved.v1` | purchase order | supplier delivery, incoming stock |
| `receipt.posted.v1` | receipt | inventory, supplier metrics, accounting |
| `transfer.shipped.v1` | transfer | inventory, warehouse tasks |
| `transfer.received.v1` | transfer | inventory, reconciliation |
| `document.indexed.v1` | document version | retrieval catalog |
| `forecast.run_completed.v1` | forecast run | replenishment, exceptions |
| `forecast.drift_detected.v1` | model cohort | model administration |
| `model.promoted.v1` | model release | routing, audit |
| `action_proposal.approved.v1` | action proposal | command executor |

Failed delivery enters a dead-letter stream with error class, attempts, first/last failure time, and replay eligibility. Replay tooling never changes the original envelope and is itself audited.

The Phase 3 foundation also produces `purchase_order.created`, `purchase_order.state_changed`, `order.created`, `order.state_changed`, `warehouse_task.created`, and `warehouse_task.state_changed`. The required approval and confirmation transitions retain the canonical `purchase_order.approved` and `order.confirmed` topics.
