# ADR 0007: integration ownership

Status: accepted

SmartStock owns inventory, allocation, purchasing, warehouse execution, and operational state. Shopify owns storefront and checkout; QBO/Xero own reconciled accounting; ShipStation owns carrier execution; Stripe collects B2B payment. Connector field-ownership matrices are explicit, and reconciliation—not last-write-wins—resolves divergence.
