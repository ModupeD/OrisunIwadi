"""Interfaces the use cases depend on. Adapters implement them; the core never imports adapters."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple, Protocol

from ..domain.dataset import Dataset
from ..domain.records import QuarantinedRow


class Source(Protocol):
    """Where a dataset's records come from (an HTTP API, a file, a test fake)."""

    dataset: Dataset

    def fetch(self, max_records: int | None = None) -> Iterable[dict[str, Any]]: ...

    def to_row(self, record: dict[str, Any]) -> dict[str, Any]:
        """Map one raw record to a row with exactly the dataset's columns."""
        ...


class RawSnapshotStore(Protocol):
    """Immutable storage for raw snapshots (the bronze zone)."""

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Store data under key unless it already exists. Returns True if written."""
        ...

    def uri(self, key: str) -> str: ...


class UpsertCounts(NamedTuple):
    inserted: int
    updated: int


class CuratedStore(Protocol):
    """Typed, queryable tables (the silver zone)."""

    def upsert(self, dataset: Dataset, rows: list[dict[str, Any]], raw_uri: str) -> UpsertCounts:
        """Insert new rows, update changed rows, leave unchanged rows alone. Atomic."""
        ...


class QuarantineStore(Protocol):
    """Rows that failed a quality rule, kept with their reasons for someone to review."""

    def put(self, dataset: Dataset, rows: list[QuarantinedRow], raw_uri: str) -> int:
        """Record failed rows. A row already recorded is not added again. Returns new rows."""
        ...
