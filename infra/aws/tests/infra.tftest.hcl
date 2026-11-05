# Offline checks of the infrastructure rules: AWS is mocked, so no account or cost.
#   terraform -chdir=infra/aws test     (or: make tf-check)

mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
  }
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "123456789012" }
  }
  override_data {
    target = data.aws_partition.current
    values = { partition = "aws" }
  }
  override_data {
    target = data.aws_ecr_repository.app
    values = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/oncolake"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/oncolake"
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "oncolake.abc.us-east-1.rds.amazonaws.com"
      port    = 5432
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-1"
        kms_key_id    = ""
        secret_status = "active"
      }]
    }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:cluster/oncolake" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn                  = "arn:aws:ecs:us-east-1:123456789012:task-definition/oncolake-x:1"
      arn_without_revision = "arn:aws:ecs:us-east-1:123456789012:task-definition/oncolake-x"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::oncolake-raw" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/oncolake" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:oncolake-alerts" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/oncolake" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:oncolake/x" }
  }
  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/oncolake-api/1"
      arn_suffix = "app/oncolake-api/1"
      dns_name   = "oncolake-api-1.us-east-1.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/oncolake-api/1"
      arn_suffix = "targetgroup/oncolake-api/1"
    }
  }
  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/oncolake-api/1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

mock_provider "random" {}

variables {
  image_tag   = "abc123def456"
  alert_email = "alerts@example.com"
}

run "default_uses_two_zones_and_one_nat" {
  command = apply

  assert {
    condition     = length(aws_subnet.private) == 2 && length(aws_subnet.database) == 2
    error_message = "Tasks and the database must have subnets in two zones."
  }
  assert {
    condition     = length(aws_nat_gateway.main) == 1 && !aws_db_instance.main.multi_az
    error_message = "The default (cheaper) setup has one NAT gateway and a single-zone database."
  }
  assert {
    condition     = aws_ecs_service.api.desired_count >= 2
    error_message = "The API must run at least two copies."
  }
}

run "high_availability_survives_a_zone" {
  command = apply
  variables {
    high_availability = true
  }

  assert {
    condition     = length(aws_nat_gateway.main) == 2 && aws_db_instance.main.multi_az
    error_message = "High availability needs a NAT gateway per zone and a Multi-AZ database."
  }
  assert {
    condition     = aws_route_table.private[0].route != aws_route_table.private[1].route
    error_message = "Each zone should route through its own NAT gateway."
  }
}

run "database_is_private_and_encrypted" {
  command = apply

  assert {
    condition     = !aws_db_instance.main.publicly_accessible && aws_db_instance.main.storage_encrypted
    error_message = "The database must be private and encrypted."
  }
  assert {
    condition     = aws_db_instance.main.manage_master_user_password
    error_message = "The admin password must be managed by RDS (never stored in state)."
  }
  assert {
    condition     = length(aws_route_table.database.route) == 0
    error_message = "Database subnets must have no route to the internet."
  }
}

run "least_privilege_roles" {
  command = apply

  assert {
    condition     = aws_ecs_task_definition.task["api"].task_role_arn == null
    error_message = "The API must have no AWS permissions (no task role)."
  }
  assert {
    condition     = alltrue([for t in aws_ecs_task_definition.task : jsondecode(t.container_definitions)[0].readonlyRootFilesystem])
    error_message = "Every container must have a read-only filesystem."
  }
  assert {
    condition = alltrue(flatten([
      for t in aws_ecs_task_definition.task : [
        for e in jsondecode(t.container_definitions)[0].environment :
        !strcontains(e.name, "PASSWORD") && e.name != "JWT_SECRET"
      ]
    ]))
    error_message = "Passwords must come from Secrets Manager, never plain environment values."
  }
}

run "https_when_a_certificate_is_given" {
  command = apply
  variables {
    certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/abc"
  }

  assert {
    condition     = length(aws_lb_listener.https) == 1
    error_message = "With a certificate there must be an HTTPS listener."
  }
  assert {
    condition     = aws_lb_listener.http.default_action[0].type == "redirect"
    error_message = "With HTTPS, plain HTTP must redirect."
  }
}

run "connection_budget_is_enforced" {
  command = plan
  variables {
    api_max_count    = 5
    api_db_pool_size = 5
  }
  expect_failures = [var.api_max_count]
}

run "at_least_two_api_copies" {
  command = plan
  variables {
    api_min_count = 1
  }
  expect_failures = [var.api_min_count]
}

run "sign_in_is_rate_limited" {
  command = apply

  assert {
    condition     = length(aws_wafv2_web_acl_association.api) == 1
    error_message = "The WAF (sign-in rate limit) must protect the load balancer by default."
  }
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/health"
    error_message = "Health checks must use /health, so a database outage doesn't restart every API copy."
  }
}
