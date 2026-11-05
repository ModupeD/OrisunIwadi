# Secrets live in Secrets Manager and are handed to each task at start-up.
#
# The values are generated as ephemeral values and written with write-only arguments, so they
# are never saved in Terraform state. Bump secrets_version to generate new ones; then run
# make aws-db-roles (database passwords) and make aws-apply (restarts the API).

variable "secrets_version" {
  description = "Increase to rotate the database passwords and the token-signing key."
  type        = number
  default     = 1
}

ephemeral "random_password" "etl_db" {
  length  = 40
  special = false
}

ephemeral "random_password" "api_db" {
  length  = 40
  special = false
}

ephemeral "random_password" "jwt" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "etl_db" {
  name_prefix             = "${var.name}/db/orisun_etl-"
  description             = "Password of the pipeline's database user"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "etl_db" {
  secret_id                = aws_secretsmanager_secret.etl_db.id
  secret_string_wo         = ephemeral.random_password.etl_db.result
  secret_string_wo_version = var.secrets_version
}

resource "aws_secretsmanager_secret" "api_db" {
  name_prefix             = "${var.name}/db/orisun_api-"
  description             = "Password of the API's read-only database user"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "api_db" {
  secret_id                = aws_secretsmanager_secret.api_db.id
  secret_string_wo         = ephemeral.random_password.api_db.result
  secret_string_wo_version = var.secrets_version
}

resource "aws_secretsmanager_secret" "jwt" {
  name_prefix             = "${var.name}/api/jwt-"
  description             = "Key that signs API login tokens"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id                = aws_secretsmanager_secret.jwt.id
  secret_string_wo         = ephemeral.random_password.jwt.result
  secret_string_wo_version = var.secrets_version
}

locals {
  # Created by make aws-user (one secret per API user), read only by the admin task.
  users_secret_prefix = "${var.name}/users/"
  master_secret_arn   = aws_db_instance.main.master_user_secret[0].secret_arn
}
