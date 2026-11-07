terraform {
  required_version = ">= 1.11" # write-only secret values (kept out of state) need 1.11+

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # The bucket and region come from infra/bootstrap: make aws-init passes them in.
  backend "s3" {
    key          = "oncolake/aws.tfstate"
    encrypt      = true
    use_lockfile = true # S3-native lock: two applies can't run at once
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.aws_account_id] # refuses to run against any other account
  default_tags {
    tags = { Project = var.name, ManagedBy = "terraform", Stack = "aws" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
}
