# Local settings (.env) are available to every target.
-include .env
export

# docker-compose is called directly (standalone binary) so no Docker CLI plugin setup is needed.
COMPOSE ?= docker-compose
# Project-local Docker config (avoids the global Docker Desktop credential helper) + Colima socket.
export DOCKER_CONFIG := $(CURDIR)/.docker
export DOCKER_HOST ?= unix://$(HOME)/.colima/default/docker.sock
export DAGSTER_HOME := $(CURDIR)/.dagster_home

.PHONY: setup up db-roles down reset dev api user test lint psql

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
