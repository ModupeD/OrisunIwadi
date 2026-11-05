# One image, three kinds of task:
#   api    long-running service, 2+ copies spread over both zones, behind the load balancer
#   etl    the daily load: started by the scheduler, exits when done (no idle servers)
#   admin  one-off jobs: create the database users, add API users
#
# Every container runs as a non-root user with a read-only filesystem (only /tmp is writable).

locals {
  image = "${data.aws_ecr_repository.app.repository_url}:${var.image_tag}"

  common_env = [
    { name = "GIT_SHA", value = var.image_tag }, # recorded as code_version on every run
    { name = "AWS_DEFAULT_REGION", value = var.region },
  ]
}

resource "aws_ecs_cluster" "main" {
  name = var.name
}

resource "aws_cloudwatch_log_group" "task" {
  for_each          = local.workloads
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = 30
}

locals {
  containers = {
    api = {
      command = null # the image default: uvicorn on port 8000
      env = [
        { name = "API_POSTGRES_DSN", value = "${local.dsn_base} user=orisun_api" },
        { name = "API_DB_POOL_SIZE", value = tostring(var.api_db_pool_size) },
      ]
      secrets = [
        { name = "PGPASSWORD", valueFrom = aws_secretsmanager_secret.api_db.arn },
        { name = "JWT_SECRET", valueFrom = aws_secretsmanager_secret.jwt.arn },
      ]
      ports = [{ containerPort = 8000, protocol = "tcp" }]
    }
    etl = {
      command = ["python", "-m", "orisun_iwadi.entrypoints.dagster.run_job", "ingest_all"]
      env = [
        { name = "ETL_POSTGRES_DSN", value = "${local.dsn_base} user=orisun_etl" },
        { name = "S3_BUCKET", value = aws_s3_bucket.raw.bucket },
      ]
      secrets = [
        { name = "PGPASSWORD", valueFrom = aws_secretsmanager_secret.etl_db.arn },
      ]
      ports = []
    }
    admin = {
      # Default job: create or update the least-privilege database users (same SQL as locally).
      command = ["sh", "-c", join(" ", [
        "psql \"$POSTGRES_DSN\" -q -v ON_ERROR_STOP=1",
        "-v etl_password=\"$ETL_DB_PASSWORD\" -v api_password=\"$API_DB_PASSWORD\"",
        "-f /app/db/db-roles.sql && echo 'Database users orisun_etl and orisun_api are ready.'",
      ])]
      env = [
        { name = "POSTGRES_DSN", value = "${local.dsn_base} user=${local.db_admin_user}" },
        { name = "PGOPTIONS", value = "-c client_min_messages=warning" },
      ]
      secrets = [
        { name = "PGPASSWORD", valueFrom = "${local.master_secret_arn}:password::" },
        { name = "ETL_DB_PASSWORD", valueFrom = aws_secretsmanager_secret.etl_db.arn },
        { name = "API_DB_PASSWORD", valueFrom = aws_secretsmanager_secret.api_db.arn },
      ]
      ports = []
    }
  }

  sizes = {
    api   = { cpu = var.api_cpu, memory = var.api_memory }
    etl   = { cpu = var.etl_cpu, memory = var.etl_memory }
    admin = { cpu = 256, memory = 512 }
  }
  task_roles = {
    api   = null # no AWS permissions
    etl   = aws_iam_role.etl_task.arn
    admin = aws_iam_role.admin_task.arn
  }
}

resource "aws_ecs_task_definition" "task" {
  for_each                 = local.containers
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.sizes[each.key].cpu
  memory                   = local.sizes[each.key].memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = local.task_roles[each.key]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([{
    name                   = each.key
    image                  = local.image
    essential              = true
    command                = each.value.command
    environment            = concat(local.common_env, each.value.env)
    secrets                = each.value.secrets
    portMappings           = each.value.ports
    readonlyRootFilesystem = true
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    linuxParameters        = { initProcessEnabled = true } # forwards stop signals, reaps zombies
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.task[each.key].name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

locals {
  api_task = aws_ecs_task_definition.task["api"]
  etl_task = aws_ecs_task_definition.task["etl"]
}

resource "aws_ecs_service" "api" {
  name             = "${var.name}-api"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = local.api_task.arn
  desired_count    = var.api_min_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"
  propagate_tags   = "SERVICE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  # Rolling deploy: new copies must pass health checks before old ones stop. If they don't,
  # ECS rolls back to the last working version by itself.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  health_check_grace_period_seconds = 30
  wait_for_steady_state             = true # make aws-apply fails loudly if the deploy fails

  lifecycle {
    ignore_changes = [desired_count] # owned by autoscaling after creation
  }

  depends_on = [aws_lb_listener.http, aws_iam_role_policy.execution]
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.api_min_count
  max_capacity       = var.api_max_count
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.name}-api-cpu"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
