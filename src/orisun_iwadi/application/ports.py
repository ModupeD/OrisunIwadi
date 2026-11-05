"""Interfaces the use cases depend on. Adapters implement them; the core never imports adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any, NamedTuple, Protocol

from ..domain.dataset import Dataset
from ..domain.records import QuarantinedRow
from .provenance import RunRecord


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

    def existing_rows(self, dataset: Dataset) -> int:
        """How many rows the dataset's table has now (0 if it doesn't exist yet)."""
        ...

    def upsert(self, dataset: Dataset, rows: list[dict[str, Any]], raw_uri: str,
               run_id: str,
               approve: Callable[[UpsertCounts, int], None] | None = None) -> UpsertCounts:
        """Insert new rows, update changed rows, leave unchanged rows alone. Atomic.

        Inserted and updated rows are stamped with raw_uri and run_id (row-level lineage).
        Before committing, calls approve(counts, rows_before); if it raises, nothing is saved.
        """
        ...


class QuarantineStore(Protocol):
    """Rows that failed a quality rule, kept with their reasons for someone to review."""

    def put(self, dataset: Dataset, rows: list[QuarantinedRow], raw_uri: str) -> int:
        """Record failed rows. A row already recorded is not added again. Returns new rows."""
        ...


class CatalogStore(Protocol):
    """The metadata catalog: which datasets exist, and a log of every run (provenance)."""

    def register_dataset(self, dataset: Dataset) -> None:
        """Record the dataset's definition (source, columns, key, rules). Idempotent."""
        ...

    def record_run(self, record: RunRecord) -> None:
        """Log one dataset run. Writing the same run again replaces it (e.g. on retry)."""
        ...


class DatasetAlreadyRunning(Exception):
    """Another run is already loading this dataset."""


class RunLock(Protocol):
    """Makes sure only one run loads a given dataset at a time."""

    def hold(self, dataset_name: str) -> AbstractContextManager[None]:
        """Hold the dataset's lock for the with-block. Raises DatasetAlreadyRunning if taken."""
        ...
