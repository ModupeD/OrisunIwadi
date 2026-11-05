# One image for the API, the pipeline and admin tasks; the task's command picks which.
# Build: make image (locally) or make aws-push (to ECR, tagged with the git commit).

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm
# psql runs the database-user setup (local/db-roles.sql) from the admin task.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
# Lets the apps verify they are talking to the real RDS server (sslmode=verify-full).
ADD https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /etc/ssl/certs/rds-global-bundle.pem
RUN chmod 644 /etc/ssl/certs/rds-global-bundle.pem \
    && useradd --system --uid 10001 --no-create-home --home-dir /tmp app

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY local/db-roles.sql /app/db/db-roles.sql

ARG GIT_SHA=unknown
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    GIT_SHA=$GIT_SHA

USER 10001
EXPOSE 8000
CMD ["uvicorn", "orisun_iwadi.entrypoints.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
