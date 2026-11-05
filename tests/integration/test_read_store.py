"""The API's Postgres read store and read-only connections, against the docker-compose DB."""

import os
import uuid

import psycopg
import pytest

from orisun_iwadi.adapters.storage.postgres_read_store import PostgresReadStore
from orisun_iwadi.adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from orisun_iwadi.application.queries import DatasetQueries
from orisun_iwadi.domain.dataset import Dataset
from orisun_iwadi.domain.records import prepare_rows
from orisun_iwadi.entrypoints.api.app import _read_only

DSN = os.getenv("POSTGRES_DSN", "postgresql://orisun:orisun@localhost:5433/orisun_iwadi")


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(DSN, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (make up)")
    yield c
    c.close()


def test_api_connections_cannot_write(conn):
    with psycopg.connect(DSN) as ro:
        _read_only(ro)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            ro.execute("CREATE TABLE should_not_exist (x int)")


def test_filters_paging_and_counts_on_real_tables(conn):
    name = f"test_{uuid.uuid4().hex[:8]}"
    ds = Dataset(name=name, description="", source_url="",
                 columns=(("id", "integer"), ("site", "text")), primary_key=("id",))
    rows = [{"id": i, "site": "Breast" if i < 12 else "Eye"} for i in range(15)]
    try:
        PostgresCuratedStore(conn).upsert(ds, prepare_rows(ds, rows).rows, "mem://x", "run-1")
        with psycopg.connect(DSN) as ro:
            _read_only(ro)
            q = DatasetQueries([ds], PostgresReadStore(ro))
            page = q.rows(name, {"site": "Breast"}, limit=5, offset=10)
            assert [r["id"] for r in page["rows"]] == [10, 11]
            assert page["rows"][0]["_run_id"] == "run-1"  # lineage is exposed to researchers
            assert q.rows(name, {"id": "3"}, limit=5)["rows"][0]["site"] == "Breast"
            counts = q.group_counts(name, "site")
            assert counts["groups"] == [{"value": "Breast", "count": 12}]
            assert counts["suppressed"]["rows"] == 3
            assert q.summary(name)["row_count"] == 15
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.commit()


def test_export_reads_everything_in_batches_and_counts_match(conn):
    name = f"test_{uuid.uuid4().hex[:8]}"
    ds = Dataset(name=name, description="", source_url="",
                 columns=(("id", "integer"), ("site", "text")), primary_key=("id",))
    rows = [{"id": i, "site": "Breast" if i % 2 else "Lung"} for i in range(1234)]
    try:
        PostgresCuratedStore(conn).upsert(ds, prepare_rows(ds, rows).rows, "mem://x", "run-1")
        store = PostgresReadStore(conn)
        batches = list(store.iter_rows(ds, {"site": "Breast"}, batch_size=100))
        assert [len(b) for b in batches] == [100] * 6 + [17]  # 617 rows, never all at once
        ids = [r["id"] for b in batches for r in b]
        assert ids == sorted(ids)  # stable order: primary key
        assert store.count_rows(ds, {"site": "Breast"}) == len(ids) == 617
        plan = DatasetQueries([ds], store).plan_export(name, {"site": "Breast"})
        assert plan.row_count == 617
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.commit()
