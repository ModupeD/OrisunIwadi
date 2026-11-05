"""FastAPI app: the composition root for the API. Run with `make api`.

Endpoints and the lowest role that can use them:
  POST /auth/token                     anyone (username + password -> 30-minute JWT)
  GET  /health                         anyone      liveness: the process is up
  GET  /ready                          anyone      readiness: the database is reachable
  GET  /me                             any signed-in user
  GET  /datasets                       viewer
  GET  /datasets/{name}                viewer      summary: columns, row count, last run
  GET  /datasets/{name}/counts?by=col  viewer      grouped counts, small groups hidden
  GET  /datasets/{name}/rows           researcher  row-level data; ?col=value filters
  GET  /admin/quarantine               admin
  GET  /admin/runs                     admin

Failure handling: if the database is down, slow or overloaded, requests fail fast with
503 (and Retry-After) instead of hanging; other requests and the process keep working.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from psycopg_pool import ConnectionPool, PoolTimeout, TooManyRequests
from pydantic import BaseModel

from ...adapters.security.passwords import ScryptHasher
from ...adapters.security.tokens import InvalidToken, JwtTokens
from ...adapters.sources import ALL_SOURCES
from ...adapters.storage.postgres_read_store import PostgresReadStore
from ...adapters.storage.user_store import PostgresUserStore
from ...application.auth import Authenticate, StoredUser
from ...application.queries import MAX_PAGE, DatasetQueries, InvalidQuery, UnknownDataset
from ...domain.access import Role, User

DATASETS = [s.dataset for s in ALL_SOURCES]
PAGING = {"limit", "offset"}
log = logging.getLogger("orisun_iwadi.api")

# Also set on the orisun_api database user; repeated here so a misconfigured user is still safe.
SESSION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5s"
POOL_TIMEOUT = 3  # seconds to wait for a free connection before answering 503
POOL_MAX_WAITING = 20  # requests allowed to queue for a connection; more get 503 at once


@dataclass(frozen=True)
class Settings:
    dsn: str | None
    jwt_secret: str

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.getenv("JWT_SECRET")
        if not secret:
            raise RuntimeError("JWT_SECRET is not set (see .env.example)")
        # The API's own read-only database user (see local/db-roles.sql).
        return cls(dsn=os.environ["API_POSTGRES_DSN"], jwt_secret=secret)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: Role


class PooledUsers:
    """UserStore that borrows a pooled connection for each lookup."""

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def get(self, username: str) -> StoredUser | None:
        with self.pool.connection() as conn:
            return PostgresUserStore(conn).get(username)


def _read_only(conn: psycopg.Connection) -> None:
    # The API never writes. Read-only sessions make that a database guarantee, not a habit.
    conn.read_only = True


oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")


# --- dependencies (tests override get_queries and get_authenticate) ----------------------------

def get_tokens(request: Request) -> JwtTokens:
    return request.app.state.tokens


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


def get_queries(conn: Annotated[psycopg.Connection, Depends(get_conn)]) -> DatasetQueries:
    return DatasetQueries(DATASETS, PostgresReadStore(conn))


def get_authenticate(request: Request) -> Authenticate:
    return request.app.state.authenticate


def current_user(token: Annotated[str, Depends(oauth2)],
                 tokens: Annotated[JwtTokens, Depends(get_tokens)]) -> User:
    try:
        return tokens.decode(token)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def require(role: Role):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.role.can_act_as(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires the {role} role")
        return user
    return check


Viewer = Annotated[User, Depends(require(Role.VIEWER))]
Researcher = Annotated[User, Depends(require(Role.RESEARCHER))]
Admin = Annotated[User, Depends(require(Role.ADMIN))]
Queries = Annotated[DatasetQueries, Depends(get_queries)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
Offset = Annotated[int, Query(ge=0)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if settings.dsn:
            # open=True doesn't wait for the database: the API starts even if it is down,
            # answers 503 until it is back, then recovers by itself.
            pool = ConnectionPool(settings.dsn, min_size=1, max_size=10, configure=_read_only,
                                  kwargs={"options": SESSION_OPTIONS, "connect_timeout": 3},
                                  timeout=POOL_TIMEOUT, max_waiting=POOL_MAX_WAITING,
                                  open=True)
            app.state.pool = pool
            app.state.authenticate = Authenticate(PooledUsers(pool), ScryptHasher())
        yield
        if pool:
            pool.close()

    app = FastAPI(title="OrisunIwadi API", version="0.1.0", lifespan=lifespan,
                  description="Role-based, read-only access to curated cancer research data.")
    app.state.tokens = JwtTokens(settings.jwt_secret)
    app.state.pool = None

    @app.exception_handler(UnknownDataset)
    def _unknown(_req, exc: UnknownDataset):
        return JSONResponse({"detail": f"Unknown dataset {exc}"}, status_code=404)

    @app.exception_handler(InvalidQuery)
    def _invalid(_req, exc: InvalidQuery):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def unavailable(message: str) -> JSONResponse:
        return JSONResponse({"detail": message}, status_code=503, headers={"Retry-After": "5"})

    @app.exception_handler(psycopg.errors.QueryCanceled)
    def _too_slow(_req, exc):
        return unavailable("Query took too long. Try a smaller page or more filters.")

    @app.exception_handler(TooManyRequests)
    @app.exception_handler(PoolTimeout)
    def _busy(_req, exc):
        log.warning("database busy: %s", exc)
        return unavailable("Database is busy or unavailable. Try again shortly.")

    @app.exception_handler(psycopg.OperationalError)
    def _db_down(_req, exc):
        log.error("database unavailable: %s", exc)
        return unavailable("Database is unavailable. Try again shortly.")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["meta"],
             description="200 when this copy can serve requests. The load balancer uses it "
                         "to stop sending traffic to a copy that can't reach the database.")
    def ready(request: Request):
        pool: ConnectionPool | None = request.app.state.pool
        if pool is None:
            return unavailable("No database configured")
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return {"status": "ready"}

    @app.post("/auth/token", response_model=Token, tags=["auth"])
    def login(form: Annotated[OAuth2PasswordRequestForm, Depends()],
              authenticate: Annotated[Authenticate, Depends(get_authenticate)],
              tokens: Annotated[JwtTokens, Depends(get_tokens)]) -> Token:
        user = authenticate(form.username, form.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password",
                                headers={"WWW-Authenticate": "Bearer"})
        return Token(access_token=tokens.issue(user),
                     expires_in=int(tokens.ttl.total_seconds()), role=user.role)

    @app.get("/me", tags=["auth"])
    def me(user: Annotated[User, Depends(current_user)]) -> dict[str, str]:
        return {"username": user.username, "role": user.role}

    @app.get("/datasets", tags=["viewer"])
    def list_datasets(_: Viewer, q: Queries) -> list[dict[str, Any]]:
        return q.list_datasets()

    @app.get("/datasets/{name}", tags=["viewer"])
    def dataset_summary(name: str, _: Viewer, q: Queries) -> dict[str, Any]:
        return q.summary(name)

    @app.get("/datasets/{name}/counts", tags=["viewer"])
    def group_counts(name: str, by: str, _: Viewer, q: Queries) -> dict[str, Any]:
        return q.group_counts(name, by)

    @app.get("/datasets/{name}/rows", tags=["researcher"],
             description="Any other query parameter filters by exact value, "
                         "e.g. ?primary_site=Breast&vital_status=Alive")
    def rows(name: str, request: Request, _: Researcher, q: Queries,
             limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        filters = {k: v for k, v in request.query_params.items() if k not in PAGING}
        return q.rows(name, filters, limit, offset)

    @app.get("/admin/quarantine", tags=["admin"])
    def quarantine(_: Admin, q: Queries, dataset: str | None = None,
                   limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        return q.quarantine(dataset, limit, offset)

    @app.get("/admin/runs", tags=["admin"])
    def runs(_: Admin, q: Queries, dataset: str | None = None,
             limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
        return q.runs(dataset, limit, offset)

    return app
