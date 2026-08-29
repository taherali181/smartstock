# AWS reference infrastructure

This stack creates the Phase 1 private network and managed data-plane foundation: encrypted/versioned S3, RDS PostgreSQL 16 with managed master credentials and point-in-time backups, encrypted multi-AZ Redis, clustered Amazon MQ RabbitMQ, ECS/ECR, KMS, Secrets Manager, and CloudWatch logs.

Remote state, deployment roles, DNS/certificates, ALB/WAF, ECS task definitions/services, GPU model hosts, dashboards, and alert destinations are environment-specific layers and must be added before a production rollout. Do not put RabbitMQ credentials in checked-in tfvars; inject them from the deployment secret store.

```bash
terraform init
terraform plan \
  -var='rabbitmq_username=...' \
  -var='rabbitmq_password=...' \
  -var='redis_auth_token=...'
```
