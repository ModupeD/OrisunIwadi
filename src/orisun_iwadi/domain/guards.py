"""Safety brake for loads. A buggy transform can produce rows that pass every type check
but are wrong, and then overwrite good data. These limits catch runs that look like that
and refuse to commit them, so the last good data stays in place.

  * too many rows fail quality rules     -> probably a mapping bug or a changed source format
  * too many existing rows change at once -> probably a mapping bug
  * the source returned nothing           -> probably an outage or a broken request

A real, expected large change (e.g. the source re-issues all its records) can be loaded by
running once with NO_LIMITS.
"""

from __future__ import annotations

from dataclasses import dataclass


class LoadRejected(Exception):
    """The run looked wrong, so nothing was loaded. Retrying won't help; a person should look."""


@dataclass(frozen=True)
class LoadLimits:
    max_quarantine_rate: float = 0.20
    max_update_rate: float = 0.50
    min_rows_to_check: int = 100  # first loads and tiny tables skip the change check

    def check_quality(self, fetched: int, quarantined: int, existing_rows: int) -> None:
        if fetched == 0 and existing_rows > 0:
            raise LoadRejected(f"source returned 0 records but the table has {existing_rows}")
        rate = quarantined / fetched if fetched else 0.0
        if rate > self.max_quarantine_rate:
            raise LoadRejected(f"{rate:.0%} of records failed quality rules "
                               f"(limit {self.max_quarantine_rate:.0%})")

    def check_changes(self, existing_rows: int, updated: int) -> None:
        if existing_rows < self.min_rows_to_check:
            return
        rate = updated / existing_rows
        if rate > self.max_update_rate:
            raise LoadRejected(f"{updated} of {existing_rows} existing rows ({rate:.0%}) would "
                               f"change (limit {self.max_update_rate:.0%})")


NO_LIMITS = LoadLimits(max_quarantine_rate=1.0, max_update_rate=1.0)
