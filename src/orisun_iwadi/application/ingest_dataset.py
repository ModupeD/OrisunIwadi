"""Use case: ingest one dataset. Fetch -> raw snapshot -> map + validate -> idempotent upsert.

Rows that fail a quality rule go to the quarantine store with their reasons; the rest load.

Idempotency:
  * The raw file's key is the sha256 of its canonical content, so an identical snapshot
    is never stored twice.
  * Each row carries a hash of its values; the curated store only rewrites rows whose
    hash changed. A repeat run with the same data writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from ..domain.records import prepare_rows, raw_object_key, sha256, snapshot_bytes
from .ports import CuratedStore, QuarantineStore, RawSnapshotStore, Source


@dataclass(frozen=True)
class IngestResult:
    dataset: str
    records_fetched: int
    rows_quarantined: int
    rows_quarantined_new: int
    rows_duplicate: int
    rows_loaded: int
    rows_inserted: int
    rows_updated: int
    raw_sha256: str
    raw_uri: str
    raw_uploaded: bool

    @property
    def rows_unchanged(self) -> int:
        return self.rows_loaded - self.rows_inserted - self.rows_updated

    @property
    def quarantine_rate(self) -> float:
        return self.rows_quarantined / self.records_fetched if self.records_fetched else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rows_unchanged": self.rows_unchanged,
                "quarantine_rate": round(self.quarantine_rate, 4)}


class IngestDataset:
    def __init__(self, raw_store: RawSnapshotStore, curated_store: CuratedStore,
                 quarantine_store: QuarantineStore,
                 log: Callable[[str], None] | None = None) -> None:
        self.raw_store = raw_store
        self.curated_store = curated_store
        self.quarantine_store = quarantine_store
        self.log = log or (lambda _msg: None)

    def __call__(self, source: Source, max_records: int | None = None) -> IngestResult:
        dataset = source.dataset
        records = list(source.fetch(max_records))
        self.log(f"{dataset.name}: fetched {len(records)} records")

        # Bronze: store exactly what the source returned, before any changes.
        data = snapshot_bytes(records)
        digest = sha256(data)
        key = raw_object_key(dataset.name, digest)
        raw_uri = self.raw_store.uri(key)
        uploaded = self.raw_store.put_if_absent(key, data)
        self.log(f"{dataset.name}: raw snapshot {'stored' if uploaded else 'unchanged'} "
                 f"at {raw_uri}")

        # Silver: typed rows, validated, de-duplicated, upserted by primary key.
        prepared = prepare_rows(dataset, (source.to_row(r) for r in records))
        new_bad = self.quarantine_store.put(dataset, prepared.quarantined, raw_uri)
        if prepared.quarantined:
            self.log(f"{dataset.name}: {len(prepared.quarantined)} rows quarantined "
                     f"({new_bad} new)")
        counts = self.curated_store.upsert(dataset, prepared.rows, raw_uri)

        result = IngestResult(
            dataset=dataset.name,
            records_fetched=len(records),
            rows_quarantined=len(prepared.quarantined),
            rows_quarantined_new=new_bad,
            rows_duplicate=prepared.duplicates,
            rows_loaded=len(prepared.rows),
            rows_inserted=counts.inserted,
            rows_updated=counts.updated,
            raw_sha256=digest,
            raw_uri=raw_uri,
            raw_uploaded=uploaded,
        )
        self.log(f"{dataset.name}: {result.as_dict()}")
        return result
