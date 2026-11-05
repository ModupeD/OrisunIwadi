# The daily load: EventBridge Scheduler starts one pipeline task; it loads all five datasets and
# exits. If the task can't be started (e.g. a brief ECS problem) the scheduler retries.
# A load that runs but fails is reported by monitoring.tf.

resource "aws_scheduler_schedule" "daily_load" {
  name                         = "${var.name}-daily-load"
  description                  = "Refresh OncoLake: fetch, validate and load all five datasets"
  schedule_expression          = var.load_schedule
  schedule_expression_timezone = var.load_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = local.etl_task.arn
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1

      network_configuration {
        subnets          = aws_subnet.private[*].id
        security_groups  = [aws_security_group.etl.id]
        assign_public_ip = false
      }
    }

    retry_policy {
      maximum_retry_attempts       = 5
      maximum_event_age_in_seconds = 3600
    }
  }
}
