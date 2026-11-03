"""Runs the real adapters against the docker-compose Postgres + SeaweedFS. Skipped if not running."""

import os
import uuid

import boto3
import psycopg
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from orisun_iwadi.adapters.sources.base import ApiSource
from orisun_iwadi.adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from orisun_iwadi.adapters.storage.quarantine_store import PostgresQuarantineStore
from orisun_iwadi.adapters.storage.s3_raw_store import S3RawSnapshotStore
from orisun_iwadi.application.ingest_dataset import IngestDataset
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
                           PostgresQuarantineStore(conn))
    records = [{"id": i, "v": f"row{i}", "tags": ["a", "b"]} for i in range(100)]
    records.append({"id": "not-a-number", "v": "bad"})  # must be quarantined, not crash COPY
    try:
        first = ingest(make_source(name, records))
        assert (first.rows_inserted, first.rows_updated, first.raw_uploaded) == (100, 0, True)
        assert (first.rows_quarantined, first.rows_quarantined_new) == (1, 1)

        again = ingest(make_source(name, list(reversed(records))))
        assert (again.rows_inserted, again.rows_updated, again.rows_unchanged) == (0, 0, 100)
        assert again.raw_uploaded is False  # same content -> same S3 object
        assert (again.rows_quarantined, again.rows_quarantined_new) == (1, 0)
        reasons = conn.execute("SELECT reasons FROM quality.quarantine WHERE dataset = %s",
                               (name,)).fetchall()
        assert reasons == [(["id='not-a-number' is not a valid integer"],)]

        edited = [*records[:-1], {"id": 99, "v": "changed", "tags": ["a"]}, {"id": 100, "v": "new"}]
        third = ingest(make_source(name, edited))
        assert (third.rows_inserted, third.rows_updated, third.rows_unchanged) == (1, 1, 99)
    finally:
        conn.rollback()
        conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{name}"')
        conn.execute("DELETE FROM quality.quarantine WHERE dataset = %s", (name,))
        conn.commit()
        for obj in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"raw/{name}/").get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
