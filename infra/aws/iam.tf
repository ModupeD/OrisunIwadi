# Least privilege: every part gets its own roles, allowing only what it uses.
#
#   execution role  what ECS needs to start a task: pull the image, write its logs, read
#                   that task's own secrets (and no one else's)
#   task role       what the running code may do in AWS
#                     api    no task role at all: the API has no AWS permissions
#                     etl    read/write raw/* and exports/* in the raw bucket
#                     admin  read API-user passwords stored by make aws-user
#   scheduler       start the pipeline task, nothing else

data "aws_ecr_repository" "app" {
  name = var.name # created by infra/bootstrap
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    # Only ECS acting for this account can use these roles (confused-deputy protection).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

locals {
  workloads = {
    api   = { secrets = [aws_secretsmanager_secret.api_db.arn, aws_secretsmanager_secret.jwt.arn] }
    etl   = { secrets = [aws_secretsmanager_secret.etl_db.arn] }
    admin = { secrets = [local.master_secret_arn, aws_secretsmanager_secret.etl_db.arn, aws_secretsmanager_secret.api_db.arn] }
  }
}

# --- execution roles --------------------------------------------------------------------------

resource "aws_iam_role" "execution" {
  for_each           = local.workloads
  name               = "${var.name}-${each.key}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "execution" {
  for_each = local.workloads

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level permissions
  }
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [data.aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.task[each.key].arn}:*"]
  }
  statement {
    sid       = "ReadOwnSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = each.value.secrets
  }
}

resource "aws_iam_role_policy" "execution" {
  for_each = local.workloads
  name     = "start-task"
  role     = aws_iam_role.execution[each.key].id
  policy   = data.aws_iam_policy_document.execution[each.key].json
}

# --- task roles -------------------------------------------------------------------------------

resource "aws_iam_role" "etl_task" {
  name               = "${var.name}-etl-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "etl_task" {
  statement {
    sid       = "ReadWriteRawFiles"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
  statement {
    # CSV/Parquet download files made after each load (download_assets.py).
    sid       = "WriteDownloadFiles"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/exports/*"]
  }
  statement {
    # Needed so a missing file reads as "not found" (404) rather than "access denied" (403);
    # the pipeline checks that before writing a new snapshot.
    sid       = "ListRawFiles"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]
  }
}

resource "aws_iam_role_policy" "etl_task" {
  name   = "raw-zone"
  role   = aws_iam_role.etl_task.id
  policy = data.aws_iam_policy_document.etl_task.json
}

resource "aws_iam_role" "admin_task" {
  name               = "${var.name}-admin-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "admin_task" {
  statement {
    sid       = "ReadApiUserPasswords"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:${local.partition}:secretsmanager:${var.region}:${local.account_id}:secret:${local.users_secret_prefix}*"]
  }
}

resource "aws_iam_role_policy" "admin_task" {
  name   = "api-user-passwords"
  role   = aws_iam_role.admin_task.id
  policy = data.aws_iam_policy_document.admin_task.json
}

# --- scheduler --------------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "StartPipelineTask"
    actions   = ["ecs:RunTask"]
    resources = ["${local.etl_task.arn_without_revision}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }
  statement {
    sid       = "HandPipelineRolesToEcs"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution["etl"].arn, aws_iam_role.etl_task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "run-pipeline"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
