"""QuarantineStore on PostgreSQL: one table holding failed rows from every dataset.

A row is identified by (dataset, record_hash), so seeing the same bad row again adds nothing.
"""

from __future__ import annotations

import json

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ...domain.dataset import Dataset
from ...domain.records import QuarantinedRow

SCHEMA = "quality"
TABLE = "quarantine"


def _jsonb(obj) -> Jsonb:
    # Rows can hold values json can't encode (Decimal, datetime); store them as text.
    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=str, sort_keys=True))


class PostgresQuarantineStore:
    def __init__(self, conn: psycopg.Connection, schema: str = SCHEMA) -> None:
        self.conn = conn
        self.table = sql.Identifier(schema, TABLE)
        self.schema = sql.Identifier(schema)

    def put(self, dataset: Dataset, rows: list[QuarantinedRow], raw_uri: str) -> int:
        try:
            self._ensure_table()
            added = 0
            for q in rows:
                cur = self.conn.execute(sql.SQL("""
                    INSERT INTO {} (dataset, record_hash, primary_key, row_data, reasons, raw_s3_uri)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dataset, record_hash) DO NOTHING
                """).format(self.table), (
                    dataset.name, q.record_hash,
                    _jsonb({k: q.row.get(k) for k in dataset.primary_key}),
                    _jsonb(q.row), _jsonb(q.reasons), raw_uri,
                ))
                added += cur.rowcount
            self.conn.commit()
            return added
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_table(self) -> None:
        self.conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(self.schema))
        self.conn.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                dataset        text        NOT NULL,
                record_hash    text        NOT NULL,
                primary_key    jsonb,
                row_data       jsonb       NOT NULL,
                reasons        jsonb       NOT NULL,
                raw_s3_uri     text,
                quarantined_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (dataset, record_hash)
            )
        """).format(self.table))
