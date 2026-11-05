"""CatalogStore on PostgreSQL: schema `catalog` with a dataset registry and a run log.

catalog.datasets  one row per dataset: description, source, columns, key, quality rules.
                  Only rewritten when the definition changes.
catalog.runs      one row per (run, dataset): timings, status, raw file hash, row counts,
                  code version. A retry of the same run replaces its row.
"""

from __future__ import annotations

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ...application.provenance import RunRecord
from ...domain.dataset import Dataset
from .ddl import run_ddl, table_exists

SCHEMA = "catalog"

COUNT_COLUMNS = ("records_fetched", "rows_loaded", "rows_inserted", "rows_updated",
                 "rows_unchanged", "rows_duplicate", "rows_quarantined", "rows_quarantined_new")


class PostgresCatalogStore:
    def __init__(self, conn: psycopg.Connection, schema: str = SCHEMA) -> None:
        self.conn = conn
        self.schema_name = schema
        self.schema = sql.Identifier(schema)
        self.datasets = sql.Identifier(schema, "datasets")
        self.runs = sql.Identifier(schema, "runs")

    def register_dataset(self, dataset: Dataset) -> None:
        definition = {
            "columns": [{"name": c, "type": t} for c, t in dataset.columns],
            "primary_key": list(dataset.primary_key),
            "rules": [r.name for r in dataset.all_rules],
        }
        self._write(sql.SQL("""
            INSERT INTO {t} AS d (name, description, source_url, definition)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
                SET description = EXCLUDED.description, source_url = EXCLUDED.source_url,
                    definition = EXCLUDED.definition, updated_at = now()
                WHERE (d.description, d.source_url, d.definition)
                      IS DISTINCT FROM (EXCLUDED.description, EXCLUDED.source_url,
                                        EXCLUDED.definition)
        """).format(t=self.datasets),
            (dataset.name, dataset.description, dataset.source_url, Jsonb(definition)))

    def record_run(self, r: RunRecord) -> None:
        res = r.result
        counts = res.as_dict() if res else {}
        values = {
            "run_id": r.run.run_id, "dataset": r.dataset, "status": r.status,
            "started_at": r.started_at, "finished_at": r.finished_at,
            "code_version": r.run.code_version, "source_url": r.source_url,
            "max_records": r.max_records,
            "raw_sha256": res.raw_sha256 if res else None,
            "raw_s3_uri": res.raw_uri if res else None,
            "raw_uploaded": res.raw_uploaded if res else None,
            **{c: counts.get(c) for c in COUNT_COLUMNS},
            "error": r.error,
        }
        cols = list(values)
        self._write(sql.SQL("""
            INSERT INTO {t} ({cols}) VALUES ({vals})
            ON CONFLICT (run_id, dataset) DO UPDATE SET {updates}
        """).format(
            t=self.runs,
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            updates=sql.SQL(", ").join(sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c))
                                       for c in cols if c not in ("run_id", "dataset")),
        ), list(values.values()))

    def _write(self, query: sql.Composable, params) -> None:
        try:
            self.conn.rollback()  # start clean, e.g. after a failed load in this connection
            self._ensure_tables()
            self.conn.execute(query, params)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_tables(self) -> None:
        if table_exists(self.conn, self.schema_name, "runs"):
            return
        counts = sql.SQL(", ").join(sql.SQL("{} integer").format(sql.Identifier(c))
                                    for c in COUNT_COLUMNS)
        run_ddl(self.conn, [
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(self.schema),
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    name        text PRIMARY KEY,
                    description text,
                    source_url  text,
                    definition  jsonb NOT NULL,
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    updated_at  timestamptz NOT NULL DEFAULT now()
                )
            """).format(self.datasets),
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS {runs} (
                    run_id       text        NOT NULL,
                    dataset      text        NOT NULL REFERENCES {datasets} (name),
                    status       text        NOT NULL CHECK (status IN ('success', 'failed')),
                    started_at   timestamptz NOT NULL,
                    finished_at  timestamptz NOT NULL,
                    code_version text,
                    source_url   text,
                    max_records  integer,
                    raw_sha256   text,
                    raw_s3_uri   text,
                    raw_uploaded boolean,
                    {counts},
                    error        text,
                    PRIMARY KEY (run_id, dataset)
                )
            """).format(runs=self.runs, datasets=self.datasets, counts=counts),
            # CREATE INDEX locks writes even when the index exists, so it only runs here.
            sql.SQL("CREATE INDEX IF NOT EXISTS runs_dataset_started "
                    "ON {} (dataset, started_at DESC)").format(self.runs),
        ])
