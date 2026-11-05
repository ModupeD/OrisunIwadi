"""Each failure the system is meant to survive, made to happen for real.

Needs `make up` (Postgres + SeaweedFS, with the database users from local/db-roles.sql).
"""

import os
import time
import uuid

import dagster as dg
import psycopg
import pytest
from fastapi.testclient import TestClient

from orisun_iwadi.adapters.sources.base import ApiSource
from orisun_iwadi.adapters.storage.catalog_store import PostgresCatalogStore
from orisun_iwadi.adapters.storage.postgres_lock import PostgresRunLock
from orisun_iwadi.adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from orisun_iwadi.adapters.storage.quarantine_store import PostgresQuarantineStore
from orisun_iwadi.adapters.storage.s3_raw_store import S3RawSnapshotStore
from orisun_iwadi.application.ingest_dataset import IngestDataset, LoadRejected, RunInfo
from orisun_iwadi.domain.dataset import Dataset
from orisun_iwadi.entrypoints.api import app as api
from orisun_iwadi.entrypoints.dagster.assets import build_asset
from orisun_iwadi.entrypoints.dagster.resources import PostgresResource, S3Resource

ADMIN = os.getenv("POSTGRES_DSN", "postgresql://orisun:orisun@localhost:5433/orisun_iwadi")
ETL = os.getenv("ETL_POSTGRES_DSN")
API = os.getenv("API_POSTGRES_DSN")
BUCKET = os.getenv("S3_BUCKET", "orisun-iwadi-raw")


def connect(dsn):
    if not dsn:
        pytest.skip("ETL_POSTGRES_DSN / API_POSTGRES_DSN not set (see .env.example)")
    try:
        return psycopg.connect(dsn, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres or its database users not ready (make up)")


def demo_dataset(name):
    return Dataset(name=name, description="test", source_url="mem://",
                   columns=(("id", "integer"), ("v", "text")), primary_key=("id",))


@pytest.fixture
def name():
    n = f"test_{uuid.uuid4().hex[:8]}"
    yield n
    with psycopg.connect(ADMIN) as c:
        c.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{n}"')
        c.execute("DELETE FROM catalog.runs WHERE dataset LIKE %s", (f"{n}%",))
        c.execute("DELETE FROM catalog.datasets WHERE name LIKE %s", (f"{n}%",))
        c.execute("DELETE FROM quality.quarantine WHERE dataset LIKE %s", (f"{n}%",))
        for t in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s AND "
                           "tablename LIKE %s", (SCHEMA, f"{n}%")).fetchall():
            c.execute(f'DROP TABLE {SCHEMA}."{t[0]}"')


# --- least privilege -------------------------------------------------------------------------

def test_api_user_cannot_write_and_slow_queries_are_cut_off():
    with connect(API) as c:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("CREATE TABLE oncology.nope (x int)")
        c.rollback()
        start = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            c.execute("SELECT pg_sleep(30)")
        assert time.monotonic() - start < 10  # cut off at the 5 s statement timeout


def test_pipeline_user_cannot_see_logins():
    with connect(ETL) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT * FROM auth.users")


def test_api_can_read_tables_the_pipeline_creates_later(name):
    ds = demo_dataset(name)
    with connect(ETL) as etl:
        PostgresCuratedStore(etl).upsert(ds, [{"id": 1, "v": "a", "_record_hash": "h"}],
                                         "mem://x", "run-1")
    with connect(API) as c:
        assert c.execute(f'SELECT v FROM {SCHEMA}."{name}"').fetchall() == [("a",)]


# --- safety brake ----------------------------------------------------------------------------

def test_buggy_load_is_rolled_back_and_good_data_survives(name, s3_client):
    ds = demo_dataset(name)

    def ingest(rows, run_id):
        with connect(ETL) as c:
            use_case = IngestDataset(S3RawSnapshotStore(s3_client, BUCKET),
                                     PostgresCuratedStore(c), PostgresQuarantineStore(c),
                                     PostgresCatalogStore(c), PostgresRunLock(c))
            return use_case(ApiSource(ds, lambda _n: rows, dict), run=RunInfo(run_id=run_id))

    ingest([{"id": i, "v": "good"} for i in range(200)], f"{name}-1")
    with pytest.raises(LoadRejected):
        ingest([{"id": i, "v": "WRONG"} for i in range(200)], f"{name}-2")

    with psycopg.connect(ADMIN) as c:
        assert c.execute(f'SELECT DISTINCT v FROM {SCHEMA}."{name}"').fetchall() == [("good",)]
        status = c.execute("SELECT status, error FROM catalog.runs WHERE run_id = %s",
                           (f"{name}-2",)).fetchone()
    assert status[0] == "failed" and "LoadRejected" in status[1]


# --- isolation between datasets --------------------------------------------------------------

def test_one_broken_source_does_not_stop_the_others(name):
    if not ETL:
        pytest.skip("ETL_POSTGRES_DSN not set")

    def broken(_n):
        raise RuntimeError("bug in this source")

    good = ApiSource(demo_dataset(f"{name}_good"), lambda _n: [{"id": 1, "v": "ok"}], dict)
    bad = ApiSource(demo_dataset(f"{name}_bad"), broken, dict)
    result = dg.materialize(
        [build_asset(good, retry_policy=None), build_asset(bad, retry_policy=None)],
        resources={"postgres": PostgresResource(dsn=ETL),
                   "s3": S3Resource(bucket=BUCKET,
                                    endpoint_url=os.getenv("S3_ENDPOINT_URL",
                                                           "http://localhost:8333"))},
        raise_on_error=False,
    )
    outcome = {e.asset_key.path[-1] for e in result.get_asset_materialization_events()}
    assert outcome == {f"{name}_good"}  # the good one loaded; the broken one failed alone
    assert not result.success


# --- the API when the database is down -------------------------------------------------------

def test_api_starts_and_fails_fast_with_503_when_database_is_down():
    settings = api.Settings(dsn="postgresql://nobody:x@localhost:1/none",
                            jwt_secret="x" * 40)
    with TestClient(api.create_app(settings)) as client:
        assert client.get("/health").status_code == 200  # process is up
        start = time.monotonic()
        r = client.get("/ready")
        assert r.status_code == 503
        assert time.monotonic() - start < 6  # fails fast, doesn't hang
        login = client.post("/auth/token", data={"username": "a", "password": "b"})
        assert login.status_code == 503
