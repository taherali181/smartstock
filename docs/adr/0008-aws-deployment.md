# ADR 0008: AWS beta deployment

Status: accepted

Deploy stateless API/workers to ECS Fargate behind ALB/WAF in `us-east-1`; use RDS PostgreSQL, ElastiCache, Amazon MQ, S3/KMS, Secrets Manager, ECR, and private GPU EC2 for models. Terraform owns infrastructure. Canadian localization is supported without an initial Canadian residency guarantee. Kubernetes and Kafka are deferred until measured need.
