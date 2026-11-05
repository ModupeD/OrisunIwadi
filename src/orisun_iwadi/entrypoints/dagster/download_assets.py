"""Download files for each dataset, linked from the Dagster UI.

After a dataset loads, its `<dataset>_download` asset writes the whole cleaned table as CSV and
Parquet to S3 (exports/<dataset>/<run id>/) and puts download links in the asset's metadata, so
they show on the run page and the asset page. The links last 7 days; materialize the download
asset again (no re-fetch from the source) for fresh ones.

Kept separate from the load: if making a file fails, the loaded data is untouched, and Dagster
retries just this step.
"""

import tempfile
from datetime import UTC, datetime

import dagster as dg
import psycopg

from ...adapters.exports.s3_export_store import S3ExportStore
from ...adapters.exports.writers import build_parquet, csv_chunks
from ...adapters.sources import ALL_SOURCES
from ...adapters.storage.postgres_read_store import PostgresReadStore
from ...application.queries import EXPORT_BATCH
from ...domain.dataset import Dataset
from .assets import RETRY
from .resources import PostgresResource, S3Resource

CSV_TYPE = "text/csv; charset=utf-8"
PARQUET_TYPE = "application/vnd.apache.parquet"


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 100_000 else f"{n / 1000:.0f} KB"


def build_download_asset(ds: Dataset,
                         retry_policy: dg.RetryPolicy | None = RETRY) -> dg.AssetsDefinition:
    @dg.asset(
        name=f"{ds.name}_download",
        deps=[ds.name],
        group_name="downloads",
        description=(f"**OncoLake** · The cleaned `{ds.name}` table as CSV (opens in Excel) "
                     "and Parquet (for pandas and other analysis tools). Download links are in "
                     "the latest materialization's metadata."),
        kinds={"csv", "parquet", "s3"},
        owners=["team:oncolake"],
        tags={"platform": "oncolake", "domain": "cancer-research"},
        retry_policy=retry_policy,
    )
    def _asset(context: dg.AssetExecutionContext, s3: S3Resource,
               postgres: PostgresResource) -> dg.MaterializeResult:
        made = datetime.now(UTC)
        stamp = made.strftime("%Y%m%d")
        prefix = f"exports/{ds.name}/{context.run.run_id}"  # a retry overwrites, never duplicates
        store = S3ExportStore(s3.client(), s3.bucket)

        csv_file = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115
        parquet_file = None
        try:
            with postgres.connect() as conn:
                # One read-only snapshot: the count, the CSV and the Parquet file always agree,
                # even if a load commits while the files are being written.
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                conn.read_only = True
                read = PostgresReadStore(conn)
                rows = read.count_rows(ds, {})
                for chunk in csv_chunks(ds, read.iter_rows(ds, {}, EXPORT_BATCH)):
                    csv_file.write(chunk)
                parquet_file = build_parquet(ds, read.iter_rows(ds, {}, EXPORT_BATCH), metadata={
                    "dataset": ds.name, "source_url": ds.source_url,
                    "primary_key": ",".join(ds.primary_key), "row_count": rows,
                    "exported_at": made.isoformat(), "run_id": context.run.run_id,
                })
            csv_bytes = csv_file.tell()
            parquet_file.seek(0, 2)
            parquet_bytes = parquet_file.tell()

            csv_key, parquet_key = f"{prefix}/{ds.name}.csv", f"{prefix}/{ds.name}.parquet"
            csv_name, parquet_name = f"{ds.name}_{stamp}.csv", f"{ds.name}_{stamp}.parquet"
            store.put(csv_key, csv_file, CSV_TYPE, csv_name)
            store.put(parquet_key, parquet_file, PARQUET_TYPE, parquet_name)
        finally:
            csv_file.close()
            if parquet_file is not None:
                parquet_file.close()

        expires = made + store.link_ttl
        context.log.info(f"{ds.name}: {rows} rows written to s3://{s3.bucket}/{prefix}/")
        return dg.MaterializeResult(metadata={
            "download_csv": dg.MetadataValue.url(store.link(csv_key)),
            "download_parquet": dg.MetadataValue.url(store.link(parquet_key)),
            "rows": rows,
            "csv_size": _size(csv_bytes),
            "parquet_size": _size(parquet_bytes),
            "links_expire": expires.strftime("%Y-%m-%d %H:%M UTC"),
            "s3_location": f"s3://{s3.bucket}/{prefix}/",
        })

    return _asset


download_assets = [build_download_asset(s.dataset) for s in ALL_SOURCES]
