"""Business rules for raw snapshots and rows: hashing, naming, validation, de-dupe. No I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .dataset import Dataset
from .quality import failures


def canonical_json(obj: Any) -> str:
    """Same data -> same string, regardless of dict key order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    """Raw records as JSON Lines, sorted so record order doesn't change the hash."""
    lines = sorted(canonical_json(r) for r in records)
    return ("\n".join(lines) + "\n").encode() if lines else b""


def raw_object_key(dataset_name: str, digest: str) -> str:
    """Raw files are content-addressed: identical content always gets the same key."""
    return f"raw/{dataset_name}/sha256={digest}.jsonl.gz"


def row_hash(dataset: Dataset, row: dict[str, Any]) -> str:
    return sha256(canonical_json({c: row.get(c) for c in dataset.column_names}).encode())


@dataclass(frozen=True)
class QuarantinedRow:
    row: dict[str, Any]
    reasons: list[str]
    record_hash: str


@dataclass
class PreparedRows:
    rows: list[dict[str, Any]] = field(default_factory=list)  # valid, one per primary key
    quarantined: list[QuarantinedRow] = field(default_factory=list)  # failed a quality rule
    duplicates: int = 0  # repeated a primary key (the last one is kept)


def prepare_rows(dataset: Dataset, rows: Iterable[dict[str, Any]]) -> PreparedRows:
    """Validate rows, set aside the ones that fail, collapse duplicate keys, attach hashes."""
    out = PreparedRows()
    rules = dataset.all_rules
    by_key: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        h = row_hash(dataset, row)
        reasons = failures(rules, row)
        if reasons:
            out.quarantined.append(QuarantinedRow(row=row, reasons=reasons, record_hash=h))
            continue
        key = tuple(row.get(k) for k in dataset.primary_key)
        if key in by_key:
            out.duplicates += 1
        by_key[key] = {**row, "_record_hash": h}
    out.rows = list(by_key.values())
    return out
