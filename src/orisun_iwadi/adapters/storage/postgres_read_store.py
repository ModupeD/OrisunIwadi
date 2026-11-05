"""ReadStore on PostgreSQL. Table and column names come from Dataset definitions (checked by
the use case) and are quoted with sql.Identifier; values are always bound parameters."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from ...domain.dataset import Dataset
from .catalog_store import SCHEMA as CATALOG
from .ddl import table_exists
from .postgres_store import SCHEMA
from .quarantine_store import SCHEMA as QUALITY
from .quarantine_store import TABLE as QUARANTINE

LINEAGE_COLUMNS = ("_run_id", "_raw_s3_uri", "_first_seen_at", "_last_updated_at")


class PostgresReadStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def _all(self, query, params=()) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchall()

    def _table(self, ds: Dataset) -> sql.Identifier:
        return sql.Identifier(SCHEMA, ds.name)

    def table_stats(self, dataset: Dataset) -> dict[str, Any]:
        if not table_exists(self.conn, SCHEMA, dataset.name):
            return {"row_count": 0, "first_loaded_at": None, "last_changed_at": None}
        return self._all(sql.SQL("""
            SELECT count(*) AS row_count, min(_first_seen_at) AS first_loaded_at,
                   max(_last_updated_at) AS last_changed_at
            FROM {}""").format(self._table(dataset)))[0]

    def last_run(self, dataset_name: str) -> dict[str, Any] | None:
        if not table_exists(self.conn, CATALOG, "runs"):
            return None
        rows = self._all("""
            SELECT run_id, status, started_at, finished_at, code_version, records_fetched,
                   rows_inserted, rows_updated, rows_unchanged, rows_quarantined
            FROM catalog.runs WHERE dataset = %s ORDER BY started_at DESC LIMIT 1""",
            (dataset_name,))
        return rows[0] if rows else None

    def quarantine_count(self, dataset_name: str) -> int:
        if not table_exists(self.conn, QUALITY, QUARANTINE):
            return 0
        return self.conn.execute("SELECT count(*) FROM quality.quarantine WHERE dataset = %s",
                                 (dataset_name,)).fetchone()[0]

    def group_counts(self, dataset: Dataset, column: str) -> list[tuple[Any, int]]:
        if not table_exists(self.conn, SCHEMA, dataset.name):
            return []
        return [tuple(r) for r in self.conn.execute(sql.SQL(
            "SELECT {c}, count(*) FROM {t} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1000"
        ).format(c=sql.Identifier(column), t=self._table(dataset))).fetchall()]

    @staticmethod
    def _where(filters: dict[str, str]) -> sql.Composable:
        # Compare as text so one filter syntax works for every column type.
        return sql.SQL(" AND ").join(
            sql.SQL("{}::text = %s").format(sql.Identifier(c)) for c in filters
        ) if filters else sql.SQL("true")

    def _select(self, dataset: Dataset, filters: dict[str, str]) -> sql.Composable:
        cols = [*dataset.column_names, *LINEAGE_COLUMNS]
        return sql.SQL("SELECT {cols} FROM {t} WHERE {where} ORDER BY {pk}").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)), t=self._table(dataset),
            where=self._where(filters),
            pk=sql.SQL(", ").join(map(sql.Identifier, dataset.primary_key)))

    def rows(self, dataset: Dataset, filters: dict[str, str], limit: int,
             offset: int) -> list[dict[str, Any]]:
        if not table_exists(self.conn, SCHEMA, dataset.name):
            return []
        return self._all(sql.SQL("{} LIMIT %s OFFSET %s").format(
            self._select(dataset, filters)), [*filters.values(), limit, offset])

    def count_rows(self, dataset: Dataset, filters: dict[str, str]) -> int:
        if not table_exists(self.conn, SCHEMA, dataset.name):
            return 0
        return self.conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(
            self._table(dataset), self._where(filters)), list(filters.values())).fetchone()[0]

    def iter_rows(self, dataset: Dataset, filters: dict[str, str],
                  batch_size: int) -> Iterator[list[dict[str, Any]]]:
        if not table_exists(self.conn, SCHEMA, dataset.name):
            return
        # A server-side cursor: Postgres keeps the result and hands it over a batch at a
        # time, so memory stays flat and each fetch stays well under the query time limit.
        with self.conn.cursor(name=f"export_{dataset.name}", row_factory=dict_row) as cur:
            cur.execute(self._select(dataset, filters), list(filters.values()))
            while batch := cur.fetchmany(batch_size):
                yield batch

    def quarantine(self, dataset_name: str | None, limit: int,
                   offset: int) -> list[dict[str, Any]]:
        if not table_exists(self.conn, QUALITY, QUARANTINE):
            return []
        return self._all("""
            SELECT dataset, primary_key, reasons, row_data, raw_s3_uri, quarantined_at
            FROM quality.quarantine WHERE %(ds)s::text IS NULL OR dataset = %(ds)s
            ORDER BY quarantined_at DESC, dataset, record_hash
            LIMIT %(limit)s OFFSET %(offset)s""",
            {"ds": dataset_name, "limit": limit, "offset": offset})

    def runs(self, dataset_name: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        if not table_exists(self.conn, CATALOG, "runs"):
            return []
        return self._all("""
            SELECT * FROM catalog.runs WHERE %(ds)s::text IS NULL OR dataset = %(ds)s
            ORDER BY started_at DESC, dataset LIMIT %(limit)s OFFSET %(offset)s""",
            {"ds": dataset_name, "limit": limit, "offset": offset})
