"""CuratedStore on PostgreSQL: bulk COPY into a staging table, then one set-based upsert."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ...application.ports import UpsertCounts
from ...domain.dataset import Dataset
from .ddl import existing_columns, run_ddl

SCHEMA = "oncology"

META_COLUMNS = (
    ("_record_hash", "text NOT NULL"),
    ("_raw_s3_uri", "text"),
    ("_run_id", "text"),  # the run that last inserted or changed this row
    ("_first_seen_at", "timestamptz NOT NULL DEFAULT now()"),
    ("_last_updated_at", "timestamptz NOT NULL DEFAULT now()"),
)


class PostgresCuratedStore:
    def __init__(self, conn: psycopg.Connection, schema: str = SCHEMA) -> None:
        self.conn = conn
        self.schema = schema

    def upsert(self, dataset: Dataset, rows: list[dict[str, Any]], raw_uri: str,
               run_id: str) -> UpsertCounts:
        try:
            self._ensure_table(dataset)
            counts = self._upsert(dataset, rows, raw_uri, run_id)
            self.conn.commit()
            return counts
        except Exception:
            self.conn.rollback()
            raise

    def _table(self, dataset: Dataset) -> sql.Identifier:
        return sql.Identifier(self.schema, dataset.name)

    def _ensure_table(self, dataset: Dataset) -> None:
        """Create the table, or add columns added to the dataset later. No-op otherwise."""
        table = self._table(dataset)
        existing = existing_columns(self.conn, self.schema, dataset.name)
        if not existing:
            cols = [*dataset.columns, *META_COLUMNS]
            run_ddl(self.conn, [
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)),
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({}, PRIMARY KEY ({}))").format(
                    table,
                    sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t))
                                       for c, t in cols),
                    sql.SQL(", ").join(map(sql.Identifier, dataset.primary_key)),
                ),
            ])
            return
        # ALTER TABLE locks out readers, so only run it for columns that are really missing.
        run_ddl(self.conn, [
            sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                table, sql.Identifier(c), sql.SQL(t))
            for c, t in [*dataset.columns, ("_run_id", "text")] if c not in existing
        ])

    def _upsert(self, dataset: Dataset, rows: list[dict[str, Any]], raw_uri: str,
                run_id: str) -> UpsertCounts:
        table = self._table(dataset)
        stage = sql.Identifier(f"stage_{dataset.name}")
        jsonb = dataset.jsonb_columns
        cols = [*dataset.column_names, "_record_hash", "_raw_s3_uri", "_run_id"]
        col_list = sql.SQL(", ").join(map(sql.Identifier, cols))

        self.conn.execute(sql.SQL(
            "CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP"
        ).format(stage, table))

        with self.conn.cursor() as cur:
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(stage, col_list)) as copy:
                for row in rows:
                    copy.write_row([
                        *(Jsonb(row[c]) if c in jsonb and row.get(c) is not None else row.get(c)
                          for c in dataset.column_names),
                        row["_record_hash"], raw_uri, run_id,
                    ])

            updates = sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in cols)
            cur.execute(sql.SQL("""
                WITH changed AS (
                    INSERT INTO {table} AS t ({cols})
                    SELECT {cols} FROM {stage}
                    ON CONFLICT ({pk}) DO UPDATE
                        SET {updates}, _last_updated_at = now()
                        WHERE t._record_hash IS DISTINCT FROM EXCLUDED._record_hash
                    RETURNING (xmax = 0) AS inserted
                )
                SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted)
                FROM changed
            """).format(table=table, cols=col_list, stage=stage, updates=updates,
                        pk=sql.SQL(", ").join(map(sql.Identifier, dataset.primary_key))))
            inserted, updated = cur.fetchone()
        return UpsertCounts(inserted, updated)
