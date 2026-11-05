"""What one ingest run did, and where its data came from. Written to the metadata catalog."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RunInfo:
    """Who/what is running: the orchestrator's run ID and the code's version (git commit)."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    code_version: str = "unknown"


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


@dataclass(frozen=True)
class RunRecord:
    """One row of the run log: a run of one dataset, successful or not."""

    run: RunInfo
    dataset: str
    source_url: str
    started_at: datetime
    finished_at: datetime
    status: str  # "success" or "failed"
    max_records: int | None = None  # set when the run was deliberately partial
    result: IngestResult | None = None  # None when the run failed
    error: str | None = None
