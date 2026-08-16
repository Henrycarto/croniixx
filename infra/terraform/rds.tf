# PostgreSQL with the TimescaleDB extension, and ElastiCache for the queue.

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

# TimescaleDB is available on RDS as an installable extension from Postgres 15
# on. The alternative, self hosting Timescale on EC2, moves backup and failover
# onto us for a workload where neither is optional.
resource "aws_db_parameter_group" "main" {
  name   = local.name
  family = "postgres16"

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements,timescaledb"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "timescaledb.telemetry_level"
    value = "off"
  }

  # Wearable ingestion is write heavy and bursty. Logging anything slower than
  # a second surfaces the queries that will matter at ten times the volume.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "random_password" "database" {
  length  = 40
  special = false
}

resource "aws_db_instance" "main" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16.4"

  instance_class    = var.environment == "production" ? "db.r6g.xlarge" : "db.t4g.medium"
  allocated_storage = 200
  # Continuous wearable metrics grow steadily rather than in steps, so headroom
  # is given up front and autoscaling covers the rest.
  max_allocated_storage = 2000
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "croniixx"
  username = "croniixx"
  password = random_password.database.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.data.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false

  multi_az                = var.environment == "production"
  backup_retention_period = var.environment == "production" ? 30 : 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"

  # A record of when a patient was told to take a cytotoxic agent is not
  # something to lose to a mistyped destroy.
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${local.name}-final" : null

  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  auto_minor_version_upgrade = true
  apply_immediately          = var.environment != "production"
}

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_parameter_group" "main" {
  name   = local.name
  family = "redis7"

  # The reminder queue must never be evicted under memory pressure. Dropping a
  # queued dose to make room for a cache entry is the worst possible trade in
  # this system, so eviction is disabled and a full instance fails writes
  # loudly instead.
  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = local.name
  description          = "Croniixx reminder queue and schedule state"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.small"
  port           = 6379

  num_cache_clusters         = var.environment == "production" ? 2 : 1
  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.data.id]
  parameter_group_name = aws_elasticache_parameter_group.main.name

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # Append only persistence, matching the local compose setup. A queue that
  # empties on failover would silently drop every pending dose reminder.
  snapshot_retention_limit = var.environment == "production" ? 7 : 1
  snapshot_window          = "01:00-02:00"

  maintenance_window = "sun:04:30-sun:05:30"
  apply_immediately  = var.environment != "production"
}

# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${local.name}-database-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
}

# Redis filling up is the alarm that matters most here. With noeviction set, a
# full instance means the reminder queue stops accepting new doses.
resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name}-redis-memory"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }
}

output "database_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive = true
}
