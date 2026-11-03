# docker-compose is called directly (standalone binary) so no Docker CLI plugin setup is needed.
COMPOSE ?= docker-compose
# Project-local Docker config (avoids the global Docker Desktop credential helper) + Colima socket.
export DOCKER_CONFIG := $(CURDIR)/.docker
export DOCKER_HOST ?= unix://$(HOME)/.colima/default/docker.sock
export DAGSTER_HOME := $(CURDIR)/.dagster_home

.PHONY: setup up down reset dev test lint psql

setup:            ## install Python deps into .venv
	uv sync

up:               ## start Postgres + S3 (SeaweedFS)
	$(COMPOSE) up -d

down:             ## stop containers (data is kept)
	$(COMPOSE) down

reset:            ## stop containers AND delete all local data
	$(COMPOSE) down -v

dev:              ## open the Dagster UI at http://localhost:3000
	mkdir -p $(DAGSTER_HOME)
	uv run dagster dev

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

psql:             ## open a SQL shell on the local database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
