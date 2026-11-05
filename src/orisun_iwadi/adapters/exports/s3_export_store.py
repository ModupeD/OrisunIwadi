"""Download files on S3 (or SeaweedFS locally), handed out as time-limited links."""

from __future__ import annotations

from datetime import timedelta
from typing import IO

from boto3.s3.transfer import TransferConfig

# S3 signed links can last at most 7 days.
MAX_LINK_TTL = timedelta(days=7)

# Upload in one request, never in parts: some stores (SeaweedFS) drop the file name of a
# multipart upload, and then browsers open a CSV instead of saving it. Exports are capped at
# 200,000 rows (well under 1 GB); a single upload can be up to 5 GB.
ONE_PIECE = TransferConfig(multipart_threshold=4 * 1024**3)


class S3ExportStore:
    def __init__(self, client, bucket: str, link_ttl: timedelta = MAX_LINK_TTL) -> None:
        if link_ttl > MAX_LINK_TTL:
            raise ValueError("S3 links can last at most 7 days")
        self.client = client
        self.bucket = bucket
        self.link_ttl = link_ttl

    def put(self, key: str, file: IO[bytes], content_type: str, filename: str) -> None:
        """Upload a file. `filename` is what the browser saves it as."""
        file.seek(0)
        self.client.upload_fileobj(file, self.bucket, key, Config=ONE_PIECE, ExtraArgs={
            "ContentType": content_type,
            "ContentDisposition": f'attachment; filename="{filename}"',
        })

    def link(self, key: str) -> str:
        """A download link anyone with it can use until it expires; no login needed."""
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(self.link_ttl.total_seconds()))
