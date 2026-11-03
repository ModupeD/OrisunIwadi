"""A Source built from two plain functions, so each dataset module stays small."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ...domain.dataset import Dataset


@dataclass(frozen=True)
class ApiSource:
    dataset: Dataset
    fetch: Callable[[int | None], Iterable[dict[str, Any]]]
    to_row: Callable[[dict[str, Any]], dict[str, Any]]
