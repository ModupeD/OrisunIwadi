"""Run a Dagster job once, without the Dagster UI. On AWS the scheduler starts a task with this.

    python -m orisun_iwadi.entrypoints.dagster.run_job ingest_all

Each dataset is still its own step: one failing (after its retries) doesn't stop the others.
Exit code 0 when every step succeeded, 1 otherwise, so a failed load raises an alert.
"""

from __future__ import annotations

import logging
import sys

import dagster as dg

log = logging.getLogger("orisun_iwadi.run_job")


def run(defs: dg.Definitions, job_name: str) -> int:
    job = defs.resolve_job_def(job_name)
    result = job.execute_in_process(raise_on_error=False)
    failed = sorted({e.step_key for e in result.all_events if e.is_step_failure})
    if result.success:
        log.info("run %s succeeded", result.run_id)
        return 0
    log.error("run %s failed; failed steps: %s", result.run_id, ", ".join(failed) or "none")
    return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    from .definitions import defs  # imported here so tests can call run() with their own defs

    return run(defs, args[0] if args else "ingest_all")


if __name__ == "__main__":
    sys.exit(main())
