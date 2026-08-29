# Backup and restore runbook

## Production controls

RDS automated backups and point-in-time recovery retain at least 14 days during beta. S3 versioning and KMS encryption are mandatory. Terraform protects the database from deletion and requires a final snapshot. Configuration, model manifests, and migration revisions are retained with the application release.

## Restore drill

1. Record incident/drill ID, source region, target recovery timestamp, database identifier, application release, and operator.
2. Isolate the restored database in a private recovery security group. Never point production traffic at an unvalidated restore.
3. Restore RDS to a new identifier at the selected point in time.
4. Connect with the migration role, inspect `alembic_version`, and apply only migrations belonging to the selected application release.
5. Run tenant counts, ledger balance, projection reconciliation, outbox duplication, object-reference, and audit-continuity checks.
6. Restore or select matching versioned S3 objects and verify KMS access from the recovery task role.
7. Run two-tenant adversarial and application smoke suites against the recovery environment.
8. Record measured recovery point and elapsed recovery time. Since beta has no contractual RPO/RTO, results inform later GA commitments.
9. Destroy the isolated recovery environment through Terraform after approval and retention of the drill report.

## Required evidence

- RDS restore event and snapshot/PITR source.
- Application and migration revisions.
- Reconciliation totals by organization and ledger account.
- Missing-object and dangling-reference report.
- Security test output and approver.
- Actual recovery point and elapsed duration.
