"""Download files linked from the Dagster UI."""

import io
from datetime import timedelta

import pytest

from orisun_iwadi.adapters.exports.s3_export_store import ONE_PIECE, S3ExportStore
from orisun_iwadi.entrypoints.dagster.definitions import defs


class FakeS3:
    def __init__(self):
        self.uploads, self.presigned = [], []

    def upload_fileobj(self, file, bucket, key, Config, ExtraArgs):
        self.uploads.append((bucket, key, file.read(), Config, ExtraArgs))

    def generate_presigned_url(self, method, Params, ExpiresIn):
        self.presigned.append((method, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?signed"


def test_files_are_uploaded_in_one_piece_with_a_download_name():
    s3 = FakeS3()
    f = io.BytesIO(b"a,b\n1,2\n")
    f.read()  # even if the file was just written, the whole file is uploaded
    S3ExportStore(s3, "bucket").put("exports/x/run/x.csv", f, "text/csv", "x_20261105.csv")
    bucket, key, body, config, extra = s3.uploads[0]
    assert (bucket, key, body) == ("bucket", "exports/x/run/x.csv", b"a,b\n1,2\n")
    assert config is ONE_PIECE
    assert extra["ContentDisposition"] == 'attachment; filename="x_20261105.csv"'


def test_links_expire_and_cannot_outlive_the_s3_limit():
    s3 = FakeS3()
    assert S3ExportStore(s3, "bucket").link("exports/x.csv").startswith("https://")
    assert s3.presigned[0][2] == 7 * 24 * 3600
    with pytest.raises(ValueError):
        S3ExportStore(s3, "bucket", link_ttl=timedelta(days=8))


def test_every_dataset_gets_a_download_asset_in_the_daily_job():
    job = defs.resolve_job_def("ingest_all")
    keys = {k.to_user_string() for k in job.asset_layer.executable_asset_keys}
    loads = {k for k in keys if not k.endswith("_download")}
    assert {f"{k}_download" for k in loads} <= keys and len(loads) == 5
