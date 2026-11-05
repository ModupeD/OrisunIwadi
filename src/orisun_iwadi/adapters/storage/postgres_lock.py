"""RunLock on PostgreSQL advisory locks: one run per dataset, across every machine.

The lock belongs to the database connection, not to a transaction, so it stays held while
the stores commit along the way. If the process dies, the connection closes and Postgres
releases the lock by itself, so a crash can't leave a dataset locked forever.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from ...application.ports import DatasetAlreadyRunning

LOCK_NAMESPACE = 7_420_002  # keeps these locks apart from other advisory locks


class PostgresRunLock:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    @contextmanager
    def hold(self, dataset_name: str) -> Iterator[None]:
        args = (LOCK_NAMESPACE, dataset_name)
        got = self.conn.execute(
            "SELECT pg_try_advisory_lock(%s, hashtext(%s))", args).fetchone()[0]
        self.conn.commit()  # don't sit "idle in transaction" while the fetch runs
        if not got:
            raise DatasetAlreadyRunning(f"{dataset_name} is already being loaded by another run")
        try:
            yield
        finally:
            self.conn.rollback()  # the connection may be mid-way through a failed transaction
            self.conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", args)
            self.conn.commit()
