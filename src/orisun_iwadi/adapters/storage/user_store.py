"""UserStore on PostgreSQL: table auth.users. The API only reads it; the CLI writes it."""

from __future__ import annotations

import psycopg
from psycopg import sql

from ...application.auth import StoredUser
from ...domain.access import Role
from .ddl import run_ddl, table_exists

SCHEMA, TABLE = "auth", "users"


class PostgresUserStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def get(self, username: str) -> StoredUser | None:
        if not table_exists(self.conn, SCHEMA, TABLE):
            return None
        row = self.conn.execute(
            "SELECT username, password_hash, role, active FROM auth.users WHERE username = %s",
            (username,)).fetchone()
        return StoredUser(row[0], row[1], Role(row[2]), row[3]) if row else None

    def save(self, username: str, password_hash: str, role: Role) -> None:
        """Create the user, or reset their password and role."""
        self.ensure_table()
        self.conn.execute("""
            INSERT INTO auth.users (username, password_hash, role) VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE
                SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role,
                    active = true, updated_at = now()
        """, (username, password_hash, role.value))
        self.conn.commit()

    def deactivate(self, username: str) -> bool:
        self.ensure_table()
        cur = self.conn.execute(
            "UPDATE auth.users SET active = false, updated_at = now() WHERE username = %s",
            (username,))
        self.conn.commit()
        return cur.rowcount == 1

    def ensure_table(self) -> None:
        if table_exists(self.conn, SCHEMA, TABLE):
            return
        roles = sql.SQL(", ").join(sql.Literal(r.value) for r in Role)
        run_ddl(self.conn, [
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)),
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS auth.users (
                    username      text PRIMARY KEY CHECK (username ~ '^[a-z0-9_.-]{{3,40}}$'),
                    password_hash text NOT NULL,
                    role          text NOT NULL CHECK (role IN ({roles})),
                    active        boolean NOT NULL DEFAULT true,
                    created_at    timestamptz NOT NULL DEFAULT now(),
                    updated_at    timestamptz NOT NULL DEFAULT now()
                )
            """).format(roles=roles),
        ])
