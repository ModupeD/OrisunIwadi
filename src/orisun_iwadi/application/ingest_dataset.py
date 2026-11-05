"""Use case: ingest one dataset. Fetch -> raw snapshot -> map + validate -> idempotent upsert.

Rows that fail a quality rule go to the quarantine store with their reasons; the rest load.
Every run, successful or failed, is written to the metadata catalog.
Only one run loads a given dataset at a time; a second one stops with DatasetAlreadyRunning.
A run that looks wrong (see domain/guards.py) is rejected with LoadRejected and saves no rows.

Idempotency:
  * The raw file's key is the sha256 of its canonical content, so an identical snapshot
    is never stored twice.
  * Each row carries a hash of its values; the curated store only rewrites rows whose
    hash changed. A repeat run with the same data writes no data (only a run-log entry).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ..domain.guards import LoadLimits, LoadRejected
from ..domain.records import prepare_rows, raw_object_key, sha256, snapshot_bytes
from .ports import (
    CatalogStore,
    CuratedStore,
    DatasetAlreadyRunning,
    QuarantineStore,
    RawSnapshotStore,
    RunLock,
    Source,
)
from .provenance import IngestResult, RunInfo, RunRecord

__all__ = ["DatasetAlreadyRunning", "IngestDataset", "IngestResult", "LoadLimits",
           "LoadRejected", "RunInfo"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IngestDataset:
    def __init__(self, raw_store: RawSnapshotStore, curated_store: CuratedStore,
                 quarantine_store: QuarantineStore, catalog: CatalogStore, lock: RunLock,
                 log: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] = _utcnow) -> None:
        self.raw_store = raw_store
        self.curated_store = curated_store
        self.quarantine_store = quarantine_store
        self.catalog = catalog
        self.lock = lock
        self.log = log or (lambda _msg: None)
        self.clock = clock

    def __call__(self, source: Source, max_records: int | None = None,
                 run: RunInfo | None = None,
                 limits: LoadLimits | None = None) -> IngestResult:
        # Held from fetch to commit: otherwise a slower run that fetched earlier could
        # commit last and overwrite newer data.
        with self.lock.hold(source.dataset.name):
            return self._run(source, max_records, run or RunInfo(), limits or LoadLimits())

    def _run(self, source: Source, max_records: int | None, run: RunInfo,
             limits: LoadLimits) -> IngestResult:
        dataset = source.dataset
        started = self.clock()
        self.catalog.register_dataset(dataset)

        def record(status: str, result: IngestResult | None = None,
                   error: str | None = None) -> None:
            self.catalog.record_run(RunRecord(
                run=run, dataset=dataset.name, source_url=dataset.source_url,
                started_at=started, finished_at=self.clock(), status=status,
                max_records=max_records, result=result, error=error))

        try:
            result = self._ingest(source, max_records, run, limits)
        except Exception as e:
            record("failed", error=f"{type(e).__name__}: {e}"[:2000])
            raise
        record("success", result=result)
        return result

    def _ingest(self, source: Source, max_records: int | None, run: RunInfo,
                limits: LoadLimits) -> IngestResult:
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

        # Safety brake: refuse runs that look like a bug or an outage (nothing is saved).
        limits.check_quality(len(records), len(prepared.quarantined),
                             self.curated_store.existing_rows(dataset))
        counts = self.curated_store.upsert(
            dataset, prepared.rows, raw_uri, run.run_id,
            approve=lambda c, before: limits.check_changes(before, c.updated))

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
