"""Dagster-configurable connections to object storage and the database."""

from contextlib import contextmanager

import boto3
import dagster as dg
import psycopg


class S3Resource(dg.ConfigurableResource):
    """Raw-zone bucket. Locally endpoint_url points at SeaweedFS; on AWS it is unset.

    Credentials are never stored here: boto3 reads them from the environment locally
    and from the task's IAM role on AWS.
    """

    bucket: str
    endpoint_url: str | None = None

    def client(self):
        return boto3.client("s3", endpoint_url=self.endpoint_url or None)


class PostgresResource(dg.ConfigurableResource):
    dsn: str

    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn) as conn:
            yield conn
