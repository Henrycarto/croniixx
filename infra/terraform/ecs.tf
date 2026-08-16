# ECS Fargate cluster and one service per Croniixx backend component.

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  # Nothing here runs on Spot. A reclaimed task holding claimed reminders
  # delays doses until the visibility timeout returns them, and that is not a
  # trade worth making for compute cost.
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each = local.services

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.environment == "production" ? 90 : 14
}

# ---------------------------------------------------------------------------
# Task roles
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls secrets at task start. The task role, below, is what
# the running code holds, and it deliberately cannot read them again.
data "aws_iam_policy_document" "secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_secretsmanager_secret" "app" {
  name = "${local.name}/app"
  # Recovery window so a mistaken destroy does not immediately delete the Terra
  # signing secret, which cannot be recovered from Terra afterwards.
  recovery_window_in_days = var.environment == "production" ? 30 : 7
}

# ---------------------------------------------------------------------------
# Task definitions and services
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "service" {
  for_each = local.services

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = each.key
      image     = "${var.ecr_registry}/croniixx-${each.key}:latest"
      essential = true

      portMappings = [
        {
          containerPort = each.value.port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "SYNC_SERVICE_URL", value = "http://sync.${local.name}.local:8001" },
        { name = "ENGINE_SERVICE_URL", value = "http://engine.${local.name}.local:8002" },
        { name = "REMINDER_SERVICE_URL", value = "http://reminder-api.${local.name}.local:8003" },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:database_url::" },
        { name = "REDIS_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:redis_url::" },
        { name = "TERRA_DEV_ID", valueFrom = "${aws_secretsmanager_secret.app.arn}:terra_dev_id::" },
        { name = "TERRA_API_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:terra_api_key::" },
        { name = "TERRA_SIGNING_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:terra_signing_secret::" },
        { name = "JWT_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:jwt_secret::" },
        { name = "EXPO_ACCESS_TOKEN", valueFrom = "${aws_secretsmanager_secret.app.arn}:expo_access_token::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:${each.value.port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }
    }
  ])
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${local.name}.local"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "service" {
  for_each = local.services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_ecs_service" "service" {
  for_each = local.services

  name            = "croniixx-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.service[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }

  service_registries {
    registry_arn = aws_service_discovery_service.service[each.key].arn
  }

  # A rolling deploy keeps the old revision serving until the new one passes
  # health checks. A dose reminder missed during a deploy window is a clinical
  # event, not a blip.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60

  # The deploy pipeline pushes a new image and forces a redeploy, so Terraform
  # must not fight it by resetting the task definition on the next apply.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

variable "ecr_registry" {
  type        = string
  description = "ECR registry hostname, for example 123456789012.dkr.ecr.eu-central-1.amazonaws.com"
}

resource "aws_appautoscaling_target" "service" {
  for_each = local.services

  max_capacity       = each.value.desired_count * 4
  min_capacity       = each.value.desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.service[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = local.services

  name               = "${local.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.service[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.service[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.service[each.key].service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 65
    # Terra delivers in bursts when a batch of rings sync, so scale out fast
    # and back in slowly rather than oscillating through the burst.
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}
