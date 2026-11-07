# Local settings (.env) are available to every target.
-include .env
export

# docker-compose is called directly (standalone binary) so no Docker CLI plugin setup is needed.
COMPOSE ?= docker-compose
# Project-local Docker config (avoids the global Docker Desktop credential helper) + Colima socket.
export DOCKER_CONFIG := $(CURDIR)/.docker
export DOCKER_HOST ?= unix://$(HOME)/.colima/default/docker.sock
export DAGSTER_HOME := $(CURDIR)/.dagster_home

.PHONY: aws-check setup up db-roles down reset dev api user test lint psql image \
        aws-bootstrap aws-init aws-push aws-plan aws-apply aws-db-roles aws-user aws-load \
        aws-destroy tf-check

setup:            ## install Python deps into .venv
	uv sync

up:               ## start Postgres + S3 (SeaweedFS), then create the database users
	$(COMPOSE) up -d
	$(COMPOSE) exec -T postgres sh -c 'until pg_isready -q; do sleep 1; done'
	$(MAKE) db-roles

db-roles:         ## create/update least-privilege DB users (safe to re-run)
	@$(COMPOSE) exec -T -e PGOPTIONS="-c client_min_messages=warning" postgres \
		psql -q -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)" \
		-v etl_password="$(ETL_DB_PASSWORD)" -v api_password="$(API_DB_PASSWORD)" \
		-f - < local/db-roles.sql
	@echo "Database users orisun_etl and orisun_api are ready."

down:             ## stop containers (data is kept)
	$(COMPOSE) down

reset:            ## stop containers AND delete all local data
	$(COMPOSE) down -v

dev:              ## open the Dagster UI at http://localhost:3000
	mkdir -p $(DAGSTER_HOME)
	-uv run python -m orisun_iwadi.entrypoints.dagster.ui_branding
	uv run --env-file .env dagster dev

api:              ## start the API at http://localhost:8000 (docs at /docs)
	uv run --env-file .env uvicorn orisun_iwadi.entrypoints.api.main:app --reload --port 8000

user:             ## create or update an API user: make user NAME=alice ROLE=researcher
	uv run --env-file .env python -m orisun_iwadi.entrypoints.api.users add $(NAME) --role $(or $(ROLE),viewer)

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

psql:             ## open a SQL shell on the local database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

image:            ## build the container image locally (the same one AWS runs)
	docker build --build-arg GIT_SHA=$(IMAGE_TAG) -t $(APP_NAME):$(IMAGE_TAG) -t $(APP_NAME):local .

# --- AWS (see docs/aws.md) --------------------------------------------------------------------
# Images are tagged with the git commit, so what runs on AWS is always a committed version.
APP_NAME   ?= oncolake
AWS_REGION ?= us-east-1
IMAGE_TAG  ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null)
# .env holds the local SeaweedFS keys; AWS commands must not use them. They use your own AWS
# login instead (aws login / aws configure / AWS_PROFILE).
# Your OncoLake AWS account and the CLI profile signed in to it (aws login --profile oncolake).
# Set ONCOLAKE_AWS_ACCOUNT_ID in .env. Terraform refuses to change any other account.
ONCOLAKE_AWS_PROFILE ?= oncolake
AWS        := env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u S3_ENDPOINT_URL \
              AWS_PROFILE=$(ONCOLAKE_AWS_PROFILE) TF_VAR_aws_account_id=$(ONCOLAKE_AWS_ACCOUNT_ID)
TF_BOOT    := $(AWS) terraform -chdir=infra/bootstrap
TF         := $(AWS) terraform -chdir=infra/aws
ECR_URL     = $(shell $(TF_BOOT) output -raw ecr_repository_url)

aws-check:        ## show which AWS account the aws-* commands will use (and refuse a wrong one)
	@test -n "$(ONCOLAKE_AWS_ACCOUNT_ID)" || { echo "Set ONCOLAKE_AWS_ACCOUNT_ID in .env first."; exit 1; }
	@actual=$$($(AWS) aws sts get-caller-identity --query Account --output text) || exit 1; \
	if [ "$$actual" != "$(ONCOLAKE_AWS_ACCOUNT_ID)" ]; then \
	  echo "Profile $(ONCOLAKE_AWS_PROFILE) is account $$actual, not $(ONCOLAKE_AWS_ACCOUNT_ID). Stopping."; exit 1; fi; \
	echo "OK: profile $(ONCOLAKE_AWS_PROFILE) is account $$actual."

aws-bootstrap: aws-check ## once: create the Terraform state bucket and the image registry
	$(TF_BOOT) init
	$(TF_BOOT) apply -var region=$(AWS_REGION) -var name=$(APP_NAME)

aws-init:         ## once per machine: connect infra/aws to its state bucket
	$(TF) init -backend-config="bucket=$$($(TF_BOOT) output -raw state_bucket)" \
		-backend-config="region=$(AWS_REGION)"

aws-push: aws-check         ## build the image for the current commit and push it to ECR
	@test -z "$$(git status --porcelain)" || \
		{ echo "Commit your changes first: the image is tagged with the commit."; exit 1; }
	$(AWS) aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(firstword $(subst /, ,$(ECR_URL)))
	docker build --build-arg GIT_SHA=$(IMAGE_TAG) -t $(ECR_URL):$(IMAGE_TAG) .
	docker push $(ECR_URL):$(IMAGE_TAG)

aws-plan: aws-check         ## show what aws-apply would change
	$(TF) plan -var image_tag=$(IMAGE_TAG)

aws-apply: aws-check        ## create/update everything and deploy the pushed image
	$(TF) apply -var image_tag=$(IMAGE_TAG)

aws-db-roles: aws-check     ## create/update the database users on RDS (after the first apply)
	$(AWS) infra/scripts/run-task.sh admin

aws-user: aws-check         ## create or reset an API user on AWS: make aws-user NAME=alice ROLE=researcher
	$(AWS) infra/scripts/aws-user.sh $(NAME) $(or $(ROLE),viewer)

aws-load: aws-check         ## run the daily load on AWS now
	$(AWS) infra/scripts/run-task.sh etl

aws-destroy: aws-check      ## delete everything in infra/aws (set deletion_protection = false and apply first)
	$(TF) destroy -var image_tag=$(IMAGE_TAG)

tf-check:         ## format-check and validate the Terraform (no AWS account needed)
	terraform fmt -check -recursive infra
	$(TF_BOOT) init -backend=false -input=false >/dev/null && $(TF_BOOT) validate
	$(TF) init -backend=false -input=false >/dev/null && $(TF) validate && $(TF) test
