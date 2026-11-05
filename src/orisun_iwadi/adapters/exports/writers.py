"""Turn exported rows into CSV (streamed) or Parquet (built, then sent).

Columns are the dataset's columns plus lineage (_run_id, _raw_s3_uri, _first_seen_at,
_last_updated_at), so every exported row can be traced back to its run and raw file.
Lists/objects (jsonb columns) are written as JSON text in both formats.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import IO, Any

import pyarrow as pa
import pyarrow.parquet as pq

from ...domain.dataset import Dataset

LINEAGE = (("_run_id", "text"), ("_raw_s3_uri", "text"),
           ("_first_seen_at", "timestamptz"), ("_last_updated_at", "timestamptz"))

ARROW_TYPES = {
    "integer": pa.int64(), "bigint": pa.int64(), "smallint": pa.int64(),
    "numeric": pa.float64(), "boolean": pa.bool_(), "date": pa.date32(),
    "timestamp": pa.timestamp("us"), "timestamptz": pa.timestamp("us", tz="UTC"),
}  # everything else (text, uuid, jsonb) is a string


def export_columns(dataset: Dataset) -> list[tuple[str, str]]:
    return [*dataset.columns, *LINEAGE]


def _json(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=str, ensure_ascii=False)


# --- CSV -------------------------------------------------------------------------------------

def _csv_value(v: Any, pg_type: str) -> Any:
    if v is None:
        return ""
    if pg_type == "jsonb":
        return _json(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v) if isinstance(v, Decimal) else v


def csv_chunks(dataset: Dataset, batches: Iterable[list[dict[str, Any]]]) -> Iterator[bytes]:
    """Header first, then one chunk per batch. UTF-8 with BOM so Excel shows accents right."""
    cols = export_columns(dataset)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c for c, _ in cols])
    yield ("﻿" + buf.getvalue()).encode()
    for batch in batches:
        buf.seek(0)
        buf.truncate()
        writer.writerows([_csv_value(row.get(c), t) for c, t in cols] for row in batch)
        yield buf.getvalue().encode()


# --- Parquet ---------------------------------------------------------------------------------

def arrow_schema(dataset: Dataset, metadata: dict[str, str]) -> pa.Schema:
    return pa.schema([pa.field(c, ARROW_TYPES.get(t, pa.string()))
                      for c, t in export_columns(dataset)],
                     metadata={k: str(v) for k, v in metadata.items()})


def _arrow_value(v: Any, pg_type: str) -> Any:
    if v is None:
        return None
    if pg_type == "jsonb":
        return _json(v)
    if pg_type == "numeric":
        return float(v)
    return v if pg_type in ARROW_TYPES else str(v)  # uuid -> str


def build_parquet(dataset: Dataset, batches: Iterable[list[dict[str, Any]]],
                  metadata: dict[str, str]) -> IO[bytes]:
    """Write the whole file (one row group per batch) to a temp file and return it, rewound.
    Kept in memory up to 32 MB, on disk beyond that."""
    cols = export_columns(dataset)
    schema = arrow_schema(dataset, metadata)
    # Returned open on purpose: the caller sends it, then closes it.
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115
    try:
        with pq.ParquetWriter(out, schema, compression="zstd") as writer:
            wrote = False
            for batch in batches:
                columns = {c: [_arrow_value(r.get(c), t) for r in batch] for c, t in cols}
                writer.write_table(pa.table(columns, schema=schema))
                wrote = True
            if not wrote:
                writer.write_table(schema.empty_table())
        out.seek(0)
        return out
    except BaseException:
        out.close()  # e.g. the database failed mid-read: don't leak the half-built file
        raise
