"""Runs the real adapters against the docker-compose Postgres + SeaweedFS. Skipped if not running."""

import os
import threading
import uuid

import boto3
import psycopg
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from orisun_iwadi.adapters.sources.base import ApiSource
from orisun_iwadi.adapters.storage.catalog_store import PostgresCatalogStore
from orisun_iwadi.adapters.storage.postgres_lock import PostgresRunLock
from orisun_iwadi.adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from orisun_iwadi.adapters.storage.quarantine_store import PostgresQuarantineStore
from orisun_iwadi.adapters.storage.s3_raw_store import S3RawSnapshotStore
from orisun_iwadi.application.ingest_dataset import DatasetAlreadyRunning, IngestDataset, RunInfo
from orisun_iwadi.domain.dataset import Dataset

DSN = os.getenv("POSTGRES_DSN", "postgresql://orisun:orisun@localhost:5433/orisun_iwadi")
BUCKET = os.getenv("S3_BUCKET", "orisun-iwadi-raw")


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(DSN, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (make up)")
    yield c
    c.close()


@pytest.fixture
def s3():
    client = boto3.client(
        "s3", endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localhost:8333"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "orisun"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "orisun-local-secret"),
        region_name="us-east-1",
    )
    try:
        client.head_bucket(Bucket=BUCKET)
    except (BotoCoreError, ClientError):
        pytest.skip("S3 (SeaweedFS) not running (make up)")
    return client


def make_source(name, records):
    ds = Dataset(name=name, description="", source_url="",
                 columns=(("id", "integer"), ("v", "text"), ("tags", "jsonb")), primary_key=("id",))
    return ApiSource(dataset=ds, fetch=lambda n: records, to_row=lambda r: dict(r))


def test_second_run_changes_nothing_and_edits_are_detected(conn, s3):
    name = f"test_{uuid.uuid4().hex[:8]}"
    ingest = IngestDataset(S3RawSnapshotStore(s3, BUCKET), PostgresCuratedStore(conn),
                           PostgresQuarantineStore(conn), PostgresCatalogStore(conn),
                           PostgresRunLock(conn))
    runs = [RunInfo(run_id=f"{name}-run{i}", code_version="test") for i in (1, 2, 3)]
    records = [{"id": i, "v": f"row{i}", "tags": ["a", "b"]} for i in range(100)]
    records.append({"id": "not-a-number", "v": "bad"})  # must be quarantined, not crash COPY
    try:
        first = ingest(make_source(name, records), run=runs[0])
        assert (first.rows_inserted, first.rows_updated, first.raw_uploaded) == (100, 0, True)
        assert (first.rows_quarantined, first.rows_quarantined_new) == (1, 1)

        again = ingest(make_source(name, list(reversed(records))), run=runs[1])
        assert (again.rows_inserted, again.rows_updated, again.rows_unchanged) == (0, 0, 100)
        assert again.raw_uploaded is False  # same content -> same S3 object
        assert (again.rows_quarantined, again.rows_quarantined_new) == (1, 0)
        reasons = conn.execute("SELECT reasons FROM quality.quarantine WHERE dataset = %s",
                               (name,)).fetchall()
        assert reasons == [(["id='not-a-number' is not a valid integer"],)]

        edited = [*records[:-1], {"id": 99, "v": "changed", "tags": ["a"]}, {"id": 100, "v": "new"}]
        third = ingest(make_source(name, edited), run=runs[2])
        assert (third.rows_inserted, third.rows_updated, third.rows_unchanged) == (1, 1, 99)

        # Lineage: each row points at the run that last changed it; unchanged rows keep theirs.
        lineage = dict(conn.execute(
            f'SELECT _run_id, count(*) FROM {SCHEMA}."{name}" GROUP BY 1').fetchall())
        assert lineage == {runs[0].run_id: 99, runs[2].run_id: 2}

        # Catalog: one log row per run, with counts and the raw file it read.
        log = conn.execute("""
            SELECT run_id, status, rows_inserted, rows_updated, rows_quarantined, raw_uploaded,
                   code_version
            FROM catalog.runs WHERE dataset = %s ORDER BY started_at""", (name,)).fetchall()
        assert log == [
            (runs[0].run_id, "success", 100, 0, 1, True, "test"),
            (runs[1].run_id, "success", 0, 0, 1, False, "test"),
            (runs[2].run_id, "success", 1, 1, 0, True, "test"),  # bad row not in this input
        ]
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.execute("DELETE FROM quality.quarantine WHERE dataset = %s", (name,))
        conn.execute("DELETE FROM catalog.runs WHERE dataset = %s", (name,))
        conn.execute("DELETE FROM catalog.datasets WHERE name = %s", (name,))
        conn.commit()
        for obj in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"raw/{name}/").get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])


def test_concurrent_runs_of_one_dataset_only_one_goes_through(conn, s3):
    """Two runs on separate connections, started together: the second is refused."""

    name = f"test_{uuid.uuid4().hex[:8]}"
    first_is_fetching, second_done = threading.Event(), threading.Event()
    outcome = {}

    def slow_fetch(_n):
        first_is_fetching.set()
        second_done.wait(10)  # keep run 1 busy until run 2 has tried
        return [{"id": 1, "v": "a"}]

    def run(label, fetch):
        with psycopg.connect(DSN) as c:
            ingest = IngestDataset(S3RawSnapshotStore(s3, BUCKET), PostgresCuratedStore(c),
                                   PostgresQuarantineStore(c), PostgresCatalogStore(c),
                                   PostgresRunLock(c))
            src = make_source(name, [])
            src = ApiSource(dataset=src.dataset, fetch=fetch, to_row=dict)
            try:
                outcome[label] = ingest(src).rows_loaded
            except DatasetAlreadyRunning:
                outcome[label] = "refused"

    try:
        t1 = threading.Thread(target=run, args=("run1", slow_fetch))
        t1.start()
        assert first_is_fetching.wait(10)
        run("run2", lambda _n: [{"id": 2, "v": "b"}])
        second_done.set()
        t1.join(10)
        assert outcome == {"run1": 1, "run2": "refused"}

        # The lock is released afterwards: a new run goes through.
        run("run3", lambda _n: [{"id": 3, "v": "c"}])
        assert outcome["run3"] == 1
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.execute("DELETE FROM catalog.runs WHERE dataset = %s", (name,))
        conn.execute("DELETE FROM catalog.datasets WHERE name = %s", (name,))
        conn.commit()
        for obj in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"raw/{name}/").get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])


def test_loading_an_existing_table_does_not_lock_out_readers(conn):
    """Table setup must be a no-op on an existing table, so readers aren't blocked."""
    name = f"test_{uuid.uuid4().hex[:8]}"
    ds = make_source(name, []).dataset
    store = PostgresCuratedStore(conn)
    try:
        store._ensure_table(ds)  # first time: creates the table
        conn.commit()
        store._ensure_table(ds)  # every later run: should take no table lock
        with psycopg.connect(DSN, autocommit=True) as other:
            locks = other.execute("""
                SELECT l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
                WHERE c.relname = %s AND l.mode = 'AccessExclusiveLock'""", (name,)).fetchall()
        assert locks == []
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.commit()
