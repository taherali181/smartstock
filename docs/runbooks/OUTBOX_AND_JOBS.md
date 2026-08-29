# Outbox and durable-job runbook

Monitor queue depth, oldest message age, retry rate, dead-letter count, and outbox unpublished age by queue and organization. Payloads and logs must not include credentials or unrestricted customer data.

When delivery fails, determine whether the failure is transient, poison data, authorization drift, or a code defect. Pause only the affected consumer/tenant when possible. A replay preserves the original event ID, organization, correlation, causation, version, and payload; the replay action and operator are audited.

Never update business rows to imitate event success. Consumers first claim `(organization_id, consumer, event_id)` and apply their effect transactionally. Connector reconciliation is required after any bulk replay.
