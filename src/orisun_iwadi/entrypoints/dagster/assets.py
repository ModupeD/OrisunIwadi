"""One Dagster asset per source. This is the composition root: it wires adapters into the use case."""

import dagster as dg

from ...adapters.sources import ALL_SOURCES
from ...adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from ...adapters.storage.quarantine_store import PostgresQuarantineStore
from ...adapters.storage.s3_raw_store import S3RawSnapshotStore
from ...application.ingest_dataset import IngestDataset
from ...application.ports import Source
from .resources import PostgresResource, S3Resource

# Warn when more than this share of a run's records fail a quality rule.
MAX_QUARANTINE_RATE = 0.05


class IngestConfig(dg.Config):
    max_records: int | None = None  # e.g. 200 in the launchpad for a quick test run


def build_asset(source: Source) -> dg.AssetsDefinition:
    ds = source.dataset

    @dg.asset(
        name=ds.name,
        group_name="ingestion",
        description=ds.description,
        kinds={"python", "s3", "postgres"},
        metadata={"source_url": ds.source_url, "table": f"{SCHEMA}.{ds.name}",
                  "primary_key": ", ".join(ds.primary_key)},
        retry_policy=dg.RetryPolicy(max_retries=2, delay=30),
        check_specs=[
            dg.AssetCheckSpec("quarantine_rate_ok", asset=ds.name,
                              description=f"At most {MAX_QUARANTINE_RATE:.0%} of records "
                                          "fail a quality rule."),
            dg.AssetCheckSpec("table_not_empty", asset=ds.name,
                              description="The curated table has at least one row."),
        ],
    )
    def _asset(context: dg.AssetExecutionContext, config: IngestConfig,
               s3: S3Resource, postgres: PostgresResource) -> dg.MaterializeResult:
        with postgres.connect() as conn:
            ingest = IngestDataset(
                raw_store=S3RawSnapshotStore(s3.client(), s3.bucket),
                curated_store=PostgresCuratedStore(conn),
                quarantine_store=PostgresQuarantineStore(conn),
                log=context.log.info,
            )
            result = ingest(source, max_records=config.max_records)
            table_rows = conn.execute(
                f'SELECT count(*) FROM {SCHEMA}."{ds.name}"').fetchone()[0]

        return dg.MaterializeResult(
            metadata={**result.as_dict(), "table_rows": table_rows},
            check_results=[
                dg.AssetCheckResult(
                    check_name="quarantine_rate_ok",
                    passed=result.quarantine_rate <= MAX_QUARANTINE_RATE,
                    severity=dg.AssetCheckSeverity.WARN,
                    metadata={"quarantine_rate": round(result.quarantine_rate, 4),
                              "rows_quarantined": result.rows_quarantined},
                ),
                dg.AssetCheckResult(
                    check_name="table_not_empty",
                    passed=table_rows > 0,
                    severity=dg.AssetCheckSeverity.ERROR,
                    metadata={"table_rows": table_rows},
                ),
            ],
        )

    return _asset


ingestion_assets = [build_asset(s) for s in ALL_SOURCES]
