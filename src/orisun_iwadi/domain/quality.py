"""Data-quality rules. A rule looks at one row and returns why it failed, or None if it passed.

Two kinds of rules run on every row:
  * type rules, made automatically from each column's Postgres type, so one malformed
    value (e.g. "abc" in an integer column) is quarantined instead of failing the whole load;
  * business rules, declared per dataset (allowed values, ranges, ID formats).

Value rules skip empty values. Use not_null() when a value is required.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

Row = dict[str, Any]


@dataclass(frozen=True)
class Rule:
    name: str
    check: Callable[[Row], str | None]  # returns a failure reason, or None if the row passed


def _empty(v: Any) -> bool:
    return v is None or v == ""


def not_null(col: str) -> Rule:
    return Rule(f"{col}_not_null",
                lambda r: f"{col} is required" if _empty(r.get(col)) else None)


def one_of(col: str, allowed: Collection[Any]) -> Rule:
    def check(r: Row) -> str | None:
        v = r.get(col)
        return None if _empty(v) or v in allowed else f"{col}={v!r} not in {sorted(allowed)}"
    return Rule(f"{col}_allowed_values", check)


def between(col: str, low: float | None = None, high: float | None = None) -> Rule:
    def check(r: Row) -> str | None:
        v = r.get(col)
        if _empty(v):
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None  # the type rule reports this
        if (low is not None and n < low) or (high is not None and n > high):
            return f"{col}={v!r} outside [{low}, {high}]"
        return None
    return Rule(f"{col}_range", check)


def matches(col: str, pattern: str) -> Rule:
    regex = re.compile(pattern)

    def check(r: Row) -> str | None:
        v = r.get(col)
        return None if _empty(v) or regex.fullmatch(str(v)) else f"{col}={v!r} != /{pattern}/"
    return Rule(f"{col}_format", check)


def ordered(*cols: str) -> Rule:
    """Values must not decrease left to right, e.g. ordered("low", "value", "high").

    Empty values are skipped.
    """
    def check(r: Row) -> str | None:
        try:
            vals = [(c, float(r[c])) for c in cols if not _empty(r.get(c))]
        except (TypeError, ValueError):
            return None  # the type rule reports this
        for (a, x), (b, y) in pairwise(vals):
            if x > y:
                return f"{a}={x} > {b}={y}"
        return None
    return Rule(f"{'_le_'.join(cols)}", check)


# --- type rules -------------------------------------------------------------------------------

def _is_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, str) and re.fullmatch(r"[+-]?\d+", v.strip()) is not None


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, Decimal)):
        return True
    if isinstance(v, float):
        return math.isfinite(v)
    if isinstance(v, str):
        try:
            return Decimal(v.strip()).is_finite()
        except InvalidOperation:
            return False
    return False


def _is_uuid(v: Any) -> bool:
    if isinstance(v, uuid.UUID):
        return True
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


def _is_timestamp(v: Any) -> bool:
    if isinstance(v, datetime):
        return True
    try:
        datetime.fromisoformat(str(v))
        return True
    except ValueError:
        return False


def _is_date(v: Any) -> bool:
    if isinstance(v, date):
        return True
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "integer": _is_int,
    "bigint": _is_int,
    "smallint": _is_int,
    "numeric": _is_number,
    "uuid": _is_uuid,
    "boolean": lambda v: isinstance(v, bool),
    "timestamp": _is_timestamp,
    "timestamptz": _is_timestamp,
    "date": _is_date,
}


def type_rules(columns: Iterable[tuple[str, str]]) -> list[Rule]:
    """One rule per column whose Postgres type we can check (text and jsonb accept anything)."""
    rules = []
    for col, pg_type in columns:
        is_valid = TYPE_CHECKS.get(pg_type.lower())
        if is_valid is None:
            continue

        def check(r: Row, col=col, pg_type=pg_type, is_valid=is_valid) -> str | None:
            v = r.get(col)
            return None if v is None or is_valid(v) else f"{col}={v!r} is not a valid {pg_type}"
        rules.append(Rule(f"{col}_type", check))
    return rules


def failures(rules: Iterable[Rule], row: Row) -> list[str]:
    """Every reason the row failed (empty list = row is valid)."""
    return [reason for rule in rules if (reason := rule.check(row)) is not None]
