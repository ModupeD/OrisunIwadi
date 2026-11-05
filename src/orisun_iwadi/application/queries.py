"""Read-side use cases behind the API. They check names against the dataset definitions,
so nothing from a URL reaches SQL unless it is a known dataset or column."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.dataset import Dataset

MAX_PAGE = 500
MAX_EXPORT_ROWS = 200_000  # the largest table today is ~27k rows
EXPORT_BATCH = 5_000  # rows read per database round trip while exporting


class UnknownDataset(LookupError):
    pass


class InvalidQuery(ValueError):
    pass


class ReadStore(Protocol):
    def table_stats(self, dataset: Dataset) -> dict[str, Any]:
        """row_count, first_loaded_at, last_changed_at."""
        ...

    def last_run(self, dataset_name: str) -> dict[str, Any] | None: ...

    def quarantine_count(self, dataset_name: str) -> int: ...

    def group_counts(self, dataset: Dataset, column: str) -> list[tuple[Any, int]]: ...

    def rows(self, dataset: Dataset, filters: dict[str, str], limit: int,
             offset: int) -> list[dict[str, Any]]: ...

    def quarantine(self, dataset_name: str | None, limit: int,
                   offset: int) -> list[dict[str, Any]]: ...

    def runs(self, dataset_name: str | None, limit: int, offset: int) -> list[dict[str, Any]]: ...

    def count_rows(self, dataset: Dataset, filters: dict[str, str]) -> int: ...

    def iter_rows(self, dataset: Dataset, filters: dict[str, str],
                  batch_size: int) -> Iterator[list[dict[str, Any]]]:
        """All matching rows in primary-key order, a batch at a time."""
        ...


@dataclass(frozen=True)
class ExportPlan:
    """A checked export request: what to read, and how many rows it will produce."""

    dataset: Dataset
    filters: dict[str, str]
    row_count: int


def _page(limit: int, offset: int) -> tuple[int, int]:
    if not 1 <= limit <= MAX_PAGE:
        raise InvalidQuery(f"limit must be between 1 and {MAX_PAGE}")
    if offset < 0:
        raise InvalidQuery("offset must be 0 or more")
    return limit, offset


class DatasetQueries:
    def __init__(self, datasets: list[Dataset], store: ReadStore,
                 min_group_size: int = 10) -> None:
        self.datasets = {d.name: d for d in datasets}
        self.store = store
        self.min_group_size = min_group_size

    def _get(self, name: str) -> Dataset:
        try:
            return self.datasets[name]
        except KeyError:
            raise UnknownDataset(name) from None

    def _check_column(self, ds: Dataset, column: str) -> None:
        if column not in ds.column_names:
            raise InvalidQuery(f"{ds.name} has no column {column!r}")

    # --- viewer -------------------------------------------------------------------------------

    def list_datasets(self) -> list[dict[str, Any]]:
        return [{"name": d.name, "description": d.description, "source_url": d.source_url}
                for d in self.datasets.values()]

    def summary(self, name: str) -> dict[str, Any]:
        ds = self._get(name)
        return {
            "name": ds.name,
            "description": ds.description,
            "source_url": ds.source_url,
            "primary_key": list(ds.primary_key),
            "columns": [{"name": c, "type": t} for c, t in ds.columns],
            **self.store.table_stats(ds),
            "rows_quarantined": self.store.quarantine_count(ds.name),
            "last_run": self.store.last_run(ds.name),
        }

    def group_counts(self, name: str, column: str) -> dict[str, Any]:
        """Row counts per value of one column. Small groups are hidden (small-cell
        suppression), so counts can't be used to single out a few patients."""
        ds = self._get(name)
        self._check_column(ds, column)
        if column in ds.jsonb_columns:
            raise InvalidQuery(f"{column!r} holds lists or objects and can't be grouped")
        groups, hidden_groups, hidden_rows = [], 0, 0
        for value, n in self.store.group_counts(ds, column):
            if n < self.min_group_size:
                hidden_groups += 1
                hidden_rows += n
            else:
                groups.append({"value": value, "count": n})
        return {"dataset": ds.name, "column": column, "groups": groups,
                "suppressed": {"groups": hidden_groups, "rows": hidden_rows,
                               "min_group_size": self.min_group_size}}

    # --- researcher ---------------------------------------------------------------------------

    def rows(self, name: str, filters: dict[str, str], limit: int = 100,
             offset: int = 0) -> dict[str, Any]:
        ds = self._get(name)
        for col in filters:
            self._check_column(ds, col)
        limit, offset = _page(limit, offset)
        rows = self.store.rows(ds, filters, limit, offset)
        return {"dataset": ds.name, "filters": filters, "limit": limit, "offset": offset,
                "count": len(rows), "rows": rows}

    def plan_export(self, name: str, filters: dict[str, str]) -> ExportPlan:
        """Check an export before any data is sent: known dataset and columns, and a size
        cap, so a download can't run for minutes or exhaust memory."""
        ds = self._get(name)
        for col in filters:
            self._check_column(ds, col)
        n = self.store.count_rows(ds, filters)
        if n > MAX_EXPORT_ROWS:
            raise InvalidQuery(f"{n} rows match; exports are limited to {MAX_EXPORT_ROWS}. "
                               "Add filters to narrow it down.")
        return ExportPlan(dataset=ds, filters=filters, row_count=n)

    # --- admin --------------------------------------------------------------------------------

    def quarantine(self, dataset: str | None, limit: int = 100,
                   offset: int = 0) -> dict[str, Any]:
        if dataset is not None:
            self._get(dataset)
        limit, offset = _page(limit, offset)
        return {"rows": self.store.quarantine(dataset, limit, offset)}

    def runs(self, dataset: str | None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if dataset is not None:
            self._get(dataset)
        limit, offset = _page(limit, offset)
        return {"runs": self.store.runs(dataset, limit, offset)}
