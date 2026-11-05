"""One Dagster asset per source. This is the composition root: it wires adapters into the use case."""

import dagster as dg

from ...adapters.sources import ALL_SOURCES
from ...adapters.storage.catalog_store import PostgresCatalogStore
from ...adapters.storage.postgres_lock import PostgresRunLock
from ...adapters.storage.postgres_store import SCHEMA, PostgresCuratedStore
from ...adapters.storage.quarantine_store import PostgresQuarantineStore
from ...adapters.storage.s3_raw_store import S3RawSnapshotStore
from ...application.ingest_dataset import IngestDataset, LoadRejected, RunInfo
from ...application.ports import Source
from ...domain.guards import NO_LIMITS
from ..version import code_version
from .resources import PostgresResource, S3Resource

# Warn when more than this share of a run's records fail a quality rule.
MAX_QUARANTINE_RATE = 0.05


class IngestConfig(dg.Config):
    max_records: int | None = None  # e.g. 200 in the launchpad for a quick test run
    # Turn off the safety brake for one run, after checking that a large change is real.
    allow_large_changes: bool = False


RETRY = dg.RetryPolicy(max_retries=2, delay=30)


def build_asset(source: Source,
                retry_policy: dg.RetryPolicy | None = RETRY) -> dg.AssetsDefinition:
    ds = source.dataset

    @dg.asset(
        name=ds.name,
        group_name="ingestion",
        description=ds.description,
        kinds={"python", "s3", "postgres"},
        metadata={"source_url": ds.source_url, "table": f"{SCHEMA}.{ds.name}",
                  "primary_key": ", ".join(ds.primary_key)},
        # Also covers DatasetAlreadyRunning: wait for the other run, then try again.
        retry_policy=retry_policy,
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
                catalog=PostgresCatalogStore(conn),
                lock=PostgresRunLock(conn),
                log=context.log.info,
            )
            run = RunInfo(run_id=context.run.run_id, code_version=code_version())
            try:
                result = ingest(source, max_records=config.max_records, run=run,
                                limits=NO_LIMITS if config.allow_large_changes else None)
            except LoadRejected as e:
                # Retrying gives the same answer; the last good data stays in place.
                raise dg.Failure(
                    description=f"Load rejected, nothing was saved: {e}. If this change is "
                                "expected, re-run with allow_large_changes: true.",
                    allow_retries=False) from e
            table_rows = conn.execute(
                f'SELECT count(*) FROM {SCHEMA}."{ds.name}"').fetchone()[0]

        return dg.MaterializeResult(
            metadata={**result.as_dict(), "table_rows": table_rows,
                      "code_version": run.code_version},
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
