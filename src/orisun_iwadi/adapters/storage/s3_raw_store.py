"""RawSnapshotStore on S3 (or any S3-compatible store, like SeaweedFS locally)."""

from __future__ import annotations

import gzip

from botocore.exceptions import ClientError

NOT_FOUND = ("404", "NoSuchKey", "NotFound")


class S3RawSnapshotStore:
    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return False
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in NOT_FOUND:
                raise
        self.client.put_object(
            Bucket=self.bucket, Key=key,
            Body=gzip.compress(data, mtime=0),  # mtime=0 keeps the gzip bytes deterministic
            ContentType="application/x-ndjson", ContentEncoding="gzip",
        )
        return True
