"""Integration tests read connection settings from .env (if present), like the Makefile does."""

import os
import pathlib

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError

ENV_FILE = pathlib.Path(__file__).parents[2] / ".env"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


@pytest.fixture
def s3_client():
    client = boto3.client(
        "s3", endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localhost:8333"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "orisun"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "orisun-local-secret"),
        region_name="us-east-1",
    )
    try:
        client.head_bucket(Bucket=os.getenv("S3_BUCKET", "orisun-iwadi-raw"))
    except (BotoCoreError, ClientError):
        pytest.skip("S3 (SeaweedFS) not running (make up)")
    return client
