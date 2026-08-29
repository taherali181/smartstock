locals {
  name = "smartstock-${var.environment}"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  availability_zone       = var.availability_zones[count.index]
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index + 1}" }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  availability_zone = var.availability_zones[count.index]
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  tags              = { Name = "${local.name}-private-${count.index + 1}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.main]
  tags          = { Name = local.name }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_kms_key" "application" {
  description             = "SmartStock ${var.environment} application data"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_s3_bucket" "objects" {
  bucket_prefix = "${local.name}-objects-"
}

resource "aws_s3_bucket_versioning" "objects" {
  bucket = aws_s3_bucket.objects.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.application.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "objects" {
  bucket                  = aws_s3_bucket.objects.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_security_group" "application" {
  name_prefix = "${local.name}-app-"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "data" {
  name_prefix = "${local.name}-data-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.application.id]
  }
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.application.id]
  }
  ingress {
    from_port       = 5671
    to_port         = 5671
    protocol        = "tcp"
    security_groups = [aws_security_group.application.id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier                     = "${local.name}-postgres"
  engine                         = "postgres"
  engine_version                 = "16"
  instance_class                 = var.database_instance_class
  allocated_storage              = 100
  max_allocated_storage          = 1000
  storage_type                   = "gp3"
  storage_encrypted              = true
  kms_key_id                     = aws_kms_key.application.arn
  db_name                        = "smartstock"
  username                       = "smartstock_admin"
  manage_master_user_password    = true
  db_subnet_group_name           = aws_db_subnet_group.main.name
  vpc_security_group_ids         = [aws_security_group.data.id]
  multi_az                       = var.database_multi_az
  backup_retention_period        = 14
  copy_tags_to_snapshot          = true
  deletion_protection            = var.deletion_protection
  skip_final_snapshot            = false
  final_snapshot_identifier      = "${local.name}-final"
  performance_insights_enabled   = true
  auto_minor_version_upgrade     = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${local.name}-redis"
  description                = "SmartStock ephemeral cache and quotas"
  engine                     = "redis"
  node_type                  = "cache.r7g.large"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token                 = var.redis_auth_token
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.data.id]
  snapshot_retention_limit   = 0
}

resource "aws_mq_broker" "rabbitmq" {
  broker_name                = "${local.name}-rabbitmq"
  engine_type                = "RABBITMQ"
  engine_version             = "4.2"
  host_instance_type         = "mq.m7g.large"
  deployment_mode            = "CLUSTER_MULTI_AZ"
  publicly_accessible        = false
  subnet_ids                 = aws_subnet.private[*].id
  security_groups            = [aws_security_group.data.id]
  auto_minor_version_upgrade = true

  user {
    username = var.rabbitmq_username
    password = var.rabbitmq_password
  }

  logs { general = true }
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecr_repository" "services" {
  for_each             = toset(["api", "worker", "forecasting"])
  name                 = "smartstock/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.application.arn
  }
}

resource "aws_secretsmanager_secret" "job_signing" {
  name       = "${local.name}/job-signing-secret"
  kms_key_id = aws_kms_key.application.id
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["api", "worker", "forecasting"])
  name              = "/smartstock/${var.environment}/${each.key}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.application.arn
}
