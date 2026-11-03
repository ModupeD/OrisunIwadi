"""The Dataset entity: what a curated table looks like. Pure Python, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field

from .quality import Rule, not_null, type_rules


@dataclass(frozen=True)
class Dataset:
    name: str  # also the curated table name and the raw-zone prefix
    description: str
    source_url: str
    columns: tuple[tuple[str, str], ...]  # (column name, Postgres type)
    primary_key: tuple[str, ...]
    rules: tuple[Rule, ...] = field(default=(), compare=False)  # business rules

    def __post_init__(self) -> None:
        missing = set(self.primary_key) - set(self.column_names)
        if missing:
            raise ValueError(f"{self.name}: primary key columns not in columns: {missing}")

    @property
    def column_names(self) -> list[str]:
        return [c for c, _ in self.columns]

    @property
    def jsonb_columns(self) -> set[str]:
        return {c for c, t in self.columns if t.lower() == "jsonb"}

    @property
    def all_rules(self) -> list[Rule]:
        """Primary key required + column types + this dataset's business rules."""
        return [*(not_null(c) for c in self.primary_key), *type_rules(self.columns), *self.rules]
