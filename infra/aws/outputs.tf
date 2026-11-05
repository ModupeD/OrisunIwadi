output "api_url" {
  description = "Landing page; the API docs are at /docs."
  value       = "${local.https ? "https" : "http"}://${aws_lb.api.dns_name}"
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}

# Used by infra/scripts/run-task.sh to start one-off tasks.
output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "private_subnet_ids" {
  value = join(",", aws_subnet.private[*].id)
}

output "task_families" {
  value = { for k, t in aws_ecs_task_definition.task : k => t.family }
}

output "security_groups" {
  value = { etl = aws_security_group.etl.id, admin = aws_security_group.admin.id }
}

output "log_groups" {
  value = { for k, g in aws_cloudwatch_log_group.task : k => g.name }
}

output "users_secret_prefix" {
  value = local.users_secret_prefix
}
