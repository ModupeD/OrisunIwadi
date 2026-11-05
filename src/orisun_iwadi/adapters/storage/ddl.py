"""Create tables safely while several runs share one database.

Two rules:
  * Only run DDL (CREATE/ALTER) when something is actually missing. ALTER TABLE and
    CREATE INDEX lock the table even when there is nothing to do, which would block
    readers (e.g. the API) for the whole load.
  * Hold a database-wide lock while running DDL, so two runs creating the same table
    at the same moment can't collide (Postgres can fail on concurrent CREATE ... IF NOT EXISTS).
"""

from __future__ import annotations

from collections.abc import Iterable

import psycopg
from psycopg import sql

DDL_LOCK_KEY = 7_420_001  # any fixed number; shared by every run of this app


def table_exists(conn: psycopg.Connection, schema: str, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL",
                        (f'"{schema}"."{table}"',)).fetchone()[0]


def existing_columns(conn: psycopg.Connection, schema: str, table: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s", (schema, table)).fetchall()}


def run_ddl(conn: psycopg.Connection, statements: Iterable[sql.Composable]) -> None:
    """Run DDL under the app-wide lock and commit right away, so its locks are short."""
    statements = list(statements)
    if not statements:
        return
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (DDL_LOCK_KEY,))
    for stmt in statements:
        conn.execute(stmt)
    conn.commit()  # also releases the transaction-level advisory lock
