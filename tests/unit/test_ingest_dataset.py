"""The use case tested with in-memory fakes: no Docker, no network, runs in milliseconds."""

from contextlib import contextmanager

import pytest

from orisun_iwadi.adapters.sources.base import ApiSource
from orisun_iwadi.application.ingest_dataset import DatasetAlreadyRunning, IngestDataset, RunInfo
from orisun_iwadi.application.ports import UpsertCounts
from orisun_iwadi.domain.dataset import Dataset
from orisun_iwadi.domain.guards import NO_LIMITS, LoadRejected


class FakeRawStore:
    def __init__(self):
        self.objects = {}

    def uri(self, key):
        return f"mem://{key}"

    def put_if_absent(self, key, data):
        if key in self.objects:
            return False
        self.objects[key] = data
        return True


class FakeCuratedStore:
    """Mimics the Postgres store's hash-based upsert."""

    def __init__(self):
        self.tables = {}

    def existing_rows(self, dataset):
        return len(self.tables.get(dataset.name, {}))

    def upsert(self, dataset, rows, raw_uri, run_id, approve=None):
        table = self.tables.setdefault(dataset.name, {})
        changes, inserted, updated = {}, 0, 0
        for row in rows:
            key = tuple(row[k] for k in dataset.primary_key)
            if key not in table:
                inserted += 1
            elif table[key]["_record_hash"] != row["_record_hash"]:
                updated += 1
            else:
                continue
            changes[key] = {**row, "_run_id": run_id}
        counts = UpsertCounts(inserted, updated)
        if approve:
            approve(counts, len(table))  # raising here means nothing is applied
        table.update(changes)
        return counts


class FakeQuarantineStore:
    def __init__(self):
        self.rows = {}

    def put(self, dataset, rows, raw_uri):
        new = [q for q in rows if (dataset.name, q.record_hash) not in self.rows]
        self.rows.update({(dataset.name, q.record_hash): q for q in new})
        return len(new)


class FakeCatalog:
    def __init__(self):
        self.datasets, self.runs = {}, {}

    def register_dataset(self, dataset):
        self.datasets[dataset.name] = dataset

    def record_run(self, record):
        self.runs[(record.run.run_id, record.dataset)] = record


class FakeLock:
    def __init__(self):
        self.held = set()

    @contextmanager
    def hold(self, name):
        if name in self.held:
            raise DatasetAlreadyRunning(name)
        self.held.add(name)
        try:
            yield
        finally:
            self.held.discard(name)


def make_ingest(raw=None, curated=None, quarantine=None, catalog=None, lock=None):
    return IngestDataset(raw or FakeRawStore(), curated or FakeCuratedStore(),
                         quarantine or FakeQuarantineStore(), catalog or FakeCatalog(),
                         lock or FakeLock())


DS = Dataset(name="demo", description="", source_url="",
             columns=(("id", "integer"), ("v", "text")), primary_key=("id",))


def source(records):
    return ApiSource(dataset=DS, fetch=lambda n: records[:n] if n else records,
                     to_row=lambda r: {"id": r.get("id"), "v": r.get("v")})


def test_rerun_is_idempotent_and_detects_changes():
    raw, curated = FakeRawStore(), FakeCuratedStore()
    ingest = make_ingest(raw, curated)
    records = [{"id": i, "v": f"row{i}"} for i in range(10)]

    first = ingest(source(records))
    assert (first.rows_inserted, first.rows_updated, first.raw_uploaded) == (10, 0, True)

    again = ingest(source(list(reversed(records))))
    assert (again.rows_inserted, again.rows_updated, again.rows_unchanged) == (0, 0, 10)
    assert again.raw_uploaded is False
    assert len(raw.objects) == 1

    edited = [*records[:-1], {"id": 9, "v": "changed"}, {"id": 10, "v": "new"}]
    third = ingest(source(edited))
    assert (third.rows_inserted, third.rows_updated, third.rows_unchanged) == (1, 1, 9)


def test_bad_rows_are_quarantined_once_and_good_rows_still_load():
    quarantine = FakeQuarantineStore()
    ingest = make_ingest(quarantine=quarantine)
    good = [{"id": i, "v": f"row{i}"} for i in range(2, 12)]
    records = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"v": "no id"}, {"id": "x", "v": "c"},
               *good]

    first = ingest(source(records))
    assert (first.records_fetched, first.rows_quarantined, first.rows_quarantined_new,
            first.rows_duplicate, first.rows_loaded) == (14, 2, 2, 1, 11)
    assert first.quarantine_rate == 2 / 14

    again = ingest(source(records))
    assert (again.rows_quarantined, again.rows_quarantined_new) == (2, 0)
    assert len(quarantine.rows) == 2


def test_max_records_is_passed_to_source():
    result = make_ingest()(source([{"id": i} for i in range(50)]), max_records=5)
    assert result.records_fetched == 5


def test_every_run_is_logged_in_the_catalog_with_its_provenance():
    catalog, curated = FakeCatalog(), FakeCuratedStore()
    ingest = make_ingest(curated=curated, catalog=catalog)
    run = RunInfo(run_id="run-1", code_version="abc123")

    result = ingest(source([{"id": 1, "v": "a"}]), max_records=10, run=run)

    rec = catalog.runs[("run-1", "demo")]
    assert (rec.status, rec.run.code_version, rec.max_records, rec.error) == (
        "success", "abc123", 10, None)
    assert rec.result == result
    assert rec.started_at <= rec.finished_at
    assert "demo" in catalog.datasets
    assert curated.tables["demo"][(1,)]["_run_id"] == "run-1"  # row-level lineage


def test_failed_runs_are_logged_then_the_error_is_raised():
    catalog = FakeCatalog()

    def broken(_n):
        raise ConnectionError("API down")

    with pytest.raises(ConnectionError):
        make_ingest(catalog=catalog)(ApiSource(dataset=DS, fetch=broken, to_row=dict),
                                     run=RunInfo(run_id="run-2"))
    rec = catalog.runs[("run-2", "demo")]
    assert (rec.status, rec.result, rec.error) == ("failed", None, "ConnectionError: API down")


def test_second_run_of_same_dataset_is_refused_while_first_is_running():
    lock, fetched = FakeLock(), []

    def fetch(_n):
        fetched.append("first")
        # While the first run is fetching, a second run of the same dataset starts.
        with pytest.raises(DatasetAlreadyRunning):
            ingest(ApiSource(dataset=DS, fetch=lambda _n: fetched.append("second") or [],
                             to_row=dict))
        return [{"id": 1, "v": "a"}]

    ingest = make_ingest(lock=lock)
    assert ingest(ApiSource(dataset=DS, fetch=fetch, to_row=dict)).rows_loaded == 1
    assert fetched == ["first"]  # the second run never fetched or wrote anything
    assert lock.held == set()  # released afterwards, even though the first run is done


def test_lock_is_released_when_a_run_fails():
    lock = FakeLock()

    def broken(_n):
        raise ConnectionError("API down")

    with pytest.raises(ConnectionError):
        make_ingest(lock=lock)(ApiSource(dataset=DS, fetch=broken, to_row=dict))
    assert lock.held == set()


# --- safety brake ------------------------------------------------------------------------------

def load(ingest, records, **kw):
    return ingest(source(records), **kw)


def test_run_with_too_many_bad_rows_is_rejected_and_loads_nothing():
    catalog, curated = FakeCatalog(), FakeCuratedStore()
    ingest = make_ingest(curated=curated, catalog=catalog)
    records = [{"id": "broken", "v": i} for i in range(5)] + [{"id": 1, "v": "ok"}]

    with pytest.raises(LoadRejected, match="83% of records failed"):
        load(ingest, records, run=RunInfo(run_id="r1"))
    assert curated.tables.get("demo", {}) == {}
    assert catalog.runs[("r1", "demo")].status == "failed"


def test_run_that_would_change_most_existing_rows_is_rejected_and_old_data_kept():
    curated = FakeCuratedStore()
    ingest = make_ingest(curated=curated)
    load(ingest, [{"id": i, "v": "good"} for i in range(200)])

    # A buggy transform: every row "changes" to the same wrong value.
    with pytest.raises(LoadRejected, match="200 of 200 existing rows"):
        load(ingest, [{"id": i, "v": "WRONG"} for i in range(200)])
    assert {r["v"] for r in curated.tables["demo"].values()} == {"good"}

    # A small, normal change still loads.
    assert load(ingest, [{"id": i, "v": "good" if i else "fixed"} for i in range(200)]
                ).rows_updated == 1


def test_empty_source_does_not_count_as_success_when_data_exists():
    ingest = make_ingest()
    load(ingest, [{"id": 1, "v": "a"}])
    with pytest.raises(LoadRejected, match="0 records"):
        load(ingest, [])


def test_expected_large_change_can_be_loaded_with_no_limits():
    curated = FakeCuratedStore()
    ingest = make_ingest(curated=curated)
    load(ingest, [{"id": i, "v": "v1"} for i in range(200)])
    assert load(ingest, [{"id": i, "v": "v2"} for i in range(200)],
                limits=NO_LIMITS).rows_updated == 200
