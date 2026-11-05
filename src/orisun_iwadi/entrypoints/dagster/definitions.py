import os

import dagster as dg

from .assets import ingestion_assets
from .resources import PostgresResource, S3Resource

ingest_all_job = dg.define_asset_job("ingest_all", selection=dg.AssetSelection.groups("ingestion"))

defs = dg.Definitions(
    assets=ingestion_assets,
    jobs=[ingest_all_job],
    schedules=[dg.ScheduleDefinition(job=ingest_all_job, cron_schedule="0 6 * * *",
                                     execution_timezone="America/New_York")],
    resources={
        "s3": S3Resource(bucket=dg.EnvVar("S3_BUCKET"),
                         endpoint_url=os.getenv("S3_ENDPOINT_URL")),  # unset on AWS
        # The pipeline's own least-privilege database user (see local/db-roles.sql).
        "postgres": PostgresResource(dsn=dg.EnvVar("ETL_POSTGRES_DSN")),
    },
)
