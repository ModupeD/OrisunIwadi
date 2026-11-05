import os

import dagster as dg

from .assets import ingestion_assets
from .download_assets import download_assets
from .resources import PostgresResource, S3Resource

ingest_all_job = dg.define_asset_job(
    "ingest_all", selection=dg.AssetSelection.groups("ingestion", "downloads"),
    description="OncoLake daily load: fetch all five public cancer datasets, validate them, "
                "update the curated tables, and make CSV/Parquet download files. "
                "Safe to re-run at any time.",
    tags={"platform": "oncolake"},
)

defs = dg.Definitions(
    assets=[*ingestion_assets, *download_assets],
    jobs=[ingest_all_job],
    schedules=[dg.ScheduleDefinition(
        name="oncolake_daily", job=ingest_all_job, cron_schedule="0 6 * * *",
        execution_timezone="America/New_York",
        description="Refresh OncoLake every morning at 6 AM Eastern.")],
    resources={
        "s3": S3Resource(bucket=dg.EnvVar("S3_BUCKET"),
                         endpoint_url=os.getenv("S3_ENDPOINT_URL")),  # unset on AWS
        # The pipeline's own least-privilege database user (see local/db-roles.sql).
        "postgres": PostgresResource(dsn=dg.EnvVar("ETL_POSTGRES_DSN")),
    },
)
