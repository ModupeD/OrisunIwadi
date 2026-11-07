variable "aws_account_id" {
  description = "The only AWS account this may change (a safety guard against deploying to the wrong one)."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be the 12-digit account number."
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  description = "Prefix for every resource name. Must match infra/bootstrap."
  type        = string
  default     = "oncolake"
}

variable "image_tag" {
  description = "Image to run: the git commit pushed by make aws-push."
  type        = string
}

variable "alert_email" {
  description = "Gets alarms, failed-load alerts and budget warnings (confirm the first email)."
  type        = string
}

variable "high_availability" {
  description = <<-EOT
    true: survives the loss of a whole Availability Zone (Multi-AZ database with automatic
    failover, one NAT gateway per zone). About +$45/month. false: cheaper; the API still runs
    2 copies in 2 zones, but the database and outbound internet live in one zone.
  EOT
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Block accidental deletion of the database and load balancer. Turn off before make aws-destroy."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  description = "Email alert when the month's spend is forecast to pass this."
  type        = number
  default     = 120
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on your own domain. Empty = HTTP only (testing only: logins would cross the internet unencrypted)."
  type        = string
  default     = ""
}

variable "allowed_cidrs" {
  description = "Who can reach the API. Narrow this to your own IP while testing."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "db_backup_retention_days" {
  description = "Days of automatic database backups (how far back you can restore). 7 is the production default; the AWS free plan allows only 1."
  type        = number
  default     = 7
  validation {
    condition     = var.db_backup_retention_days >= 0 && var.db_backup_retention_days <= 35
    error_message = "RDS keeps automatic backups for 0 (off) to 35 days."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "cpu_architecture" {
  description = "Must match the image: ARM64 when built on an Apple Silicon Mac (and cheaper on Fargate)."
  type        = string
  default     = "ARM64"
}

variable "api_cpu" {
  type    = number
  default = 256
}

variable "api_memory" {
  type    = number
  default = 512
}

variable "api_min_count" {
  description = "API copies always running. 2+ keeps the API up if one copy or one zone fails."
  type        = number
  default     = 2
  validation {
    condition     = var.api_min_count >= 2
    error_message = "Run at least 2 API copies, so one failure doesn't take the API down."
  }
}

variable "api_max_count" {
  description = "Autoscaling ceiling. Times api_db_pool_size it must stay within the orisun_api connection limit (20)."
  type        = number
  default     = 4
  validation {
    # Otherwise, at full scale, the API copies would be refused connections by the database.
    condition     = var.api_max_count * var.api_db_pool_size <= 20
    error_message = "api_max_count x api_db_pool_size must be at most 20 (the orisun_api connection limit in local/db-roles.sql)."
  }
}

variable "api_db_pool_size" {
  description = "Database connections per API copy."
  type        = number
  default     = 5
}

variable "etl_cpu" {
  type    = number
  default = 1024
}

variable "etl_memory" {
  type    = number
  default = 2048
}

variable "load_schedule" {
  description = "When the daily load runs (EventBridge Scheduler cron)."
  type        = string
  default     = "cron(0 6 * * ? *)"
}

variable "load_timezone" {
  type    = string
  default = "America/New_York"
}
