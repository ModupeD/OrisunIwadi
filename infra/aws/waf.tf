# Web firewall in front of the load balancer:
#   - limits sign-in attempts per IP address (stops password guessing)
#   - limits total requests per IP address (one client can't flood the API)
#   - AWS-managed rules that block common attacks and known-bad inputs
# About $10/month. Set enable_waf = false to save that while experimenting.

variable "enable_waf" {
  type    = bool
  default = true
}

variable "login_attempts_per_5_min" {
  description = "Sign-in requests one IP address may make in 5 minutes before being blocked."
  type        = number
  default     = 20
}

variable "requests_per_5_min" {
  description = "Requests of any kind one IP address may make in 5 minutes."
  type        = number
  default     = 3000
}

resource "aws_wafv2_web_acl" "api" {
  count = var.enable_waf ? 1 : 0
  name  = "${var.name}-api"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "limit-sign-in-attempts"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit                 = var.login_attempts_per_5_min
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"
        scope_down_statement {
          byte_match_statement {
            search_string         = "/auth/token"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "sign-in-rate-limit"
    }
  }

  rule {
    name     = "limit-requests-per-ip"
    priority = 2
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit                 = var.requests_per_5_min
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"
      }
    }
    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "request-rate-limit"
    }
  }

  rule {
    name     = "aws-common-attacks"
    priority = 3
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
        # Sign-in is a form post and exports can be large; don't block on body size.
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 4
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-bad-inputs"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-api"
  }
}

resource "aws_wafv2_web_acl_association" "api" {
  count        = var.enable_waf ? 1 : 0
  resource_arn = aws_lb.api.arn
  web_acl_arn  = aws_wafv2_web_acl.api[0].arn
}
