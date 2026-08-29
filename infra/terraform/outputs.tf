output "vpc_id" { value = aws_vpc.main.id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "application_security_group_id" { value = aws_security_group.application.id }
output "database_endpoint" { value = aws_db_instance.postgres.address }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "rabbitmq_endpoints" { value = aws_mq_broker.rabbitmq.instances[*].endpoints }
output "object_bucket" { value = aws_s3_bucket.objects.id }
output "ecs_cluster_arn" { value = aws_ecs_cluster.main.arn }
output "ecr_repositories" { value = { for key, value in aws_ecr_repository.services : key => value.repository_url } }
