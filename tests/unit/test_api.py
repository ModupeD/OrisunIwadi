"""The API with in-memory fakes: role checks, tokens, validation. No database needed."""

import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
import pytest
from fastapi.testclient import TestClient

from orisun_iwadi.adapters.security.passwords import ScryptHasher
from orisun_iwadi.adapters.security.tokens import JwtTokens
from orisun_iwadi.application.auth import Authenticate, StoredUser
from orisun_iwadi.application.queries import DatasetQueries
from orisun_iwadi.domain.access import Role, User
from orisun_iwadi.entrypoints.api import app as api

SECRET = "test-secret-that-is-long-enough-123456"
HASHER = ScryptHasher()
PASSWORD = "correct horse battery"


class FakeUsers:
    def __init__(self):
        h = HASHER.hash(PASSWORD)
        self.users = {
            "vera": StoredUser("vera", h, Role.VIEWER),
            "rex": StoredUser("rex", h, Role.RESEARCHER),
            "ada": StoredUser("ada", h, Role.ADMIN),
            "gone": StoredUser("gone", h, Role.ADMIN, active=False),
        }

    def get(self, username):
        return self.users.get(username)


class FakeReadStore:
    def __init__(self):
        self.rows_calls = []

    def table_stats(self, dataset):
        return {"row_count": 3, "first_loaded_at": None, "last_changed_at": None}

    def last_run(self, name):
        return {"status": "success"}

    def quarantine_count(self, name):
        return 0

    def group_counts(self, dataset, column):
        return [("Breast", 120), ("Lung", 80), ("Eye", 3)]

    def rows(self, dataset, filters, limit, offset):
        self.rows_calls.append(filters)
        return [{"case_id": "x", "primary_site": "Breast"}]

    def quarantine(self, name, limit, offset):
        return [{"dataset": "gdc_tcga_cases", "reasons": ["bad"]}]

    def runs(self, name, limit, offset):
        return [{"run_id": "r1"}]

    # exports
    matching = 2
    export_batches = ([
        {"case_id": "6e3b6b72-142d-4b8d-a462-28a205796e41", "primary_site": "Breast",
         "age_at_diagnosis_days": Decimal(20000), "diagnoses": [{"stage": "IA"}],
         "source_updated_at": datetime(2024, 1, 2, tzinfo=UTC), "_run_id": "run-1",
         "_first_seen_at": datetime(2026, 11, 3, tzinfo=UTC)},
    ], [
        {"case_id": "11111111-142d-4b8d-a462-28a205796e41", "primary_site": "Breast",
         "diagnoses": None, "_run_id": "run-2"},
    ])

    def count_rows(self, dataset, filters):
        self.export_filters = filters
        return self.matching

    def read(self, plan):
        yield from self.export_batches


@pytest.fixture
def store():
    return FakeReadStore()


@pytest.fixture
def client(store):
    app = api.create_app(api.Settings(dsn=None, jwt_secret=SECRET))
    app.dependency_overrides[api.get_queries] = lambda: DatasetQueries(api.DATASETS, store)
    authenticate = Authenticate(FakeUsers(), HASHER)
    app.dependency_overrides[api.get_authenticate] = lambda: authenticate
    app.dependency_overrides[api.get_export_reader] = lambda: store.read
    with TestClient(app) as c:
        yield c


def login(client, username, password=PASSWORD):
    return client.post("/auth/token", data={"username": username, "password": password})


def auth(client, username):
    token = login(client, username).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- login ---------------------------------------------------------------------------------

def test_login_returns_a_token_with_the_users_role(client):
    r = login(client, "rex")
    assert r.status_code == 200
    assert r.json()["role"] == "researcher"
    assert client.get("/me", headers=auth(client, "rex")).json() == {
        "username": "rex", "role": "researcher"}


@pytest.mark.parametrize("username,password", [
    ("rex", "wrong password!!"), ("nobody", PASSWORD), ("gone", PASSWORD)])
def test_login_fails_the_same_way_for_bad_password_unknown_or_inactive_user(
        client, username, password):
    r = login(client, username, password)
    assert r.status_code == 401
    assert r.json() == {"detail": "Wrong username or password"}


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    jwt.encode({"sub": "x", "role": "admin", "iss": "orisun-iwadi", "exp": 9999999999},
               "some-other-secret-that-is-also-long", algorithm="HS256"),  # forged
    jwt.encode({"sub": "x", "role": "admin", "iss": "orisun-iwadi", "exp": 9999999999},
               None, algorithm="none"),  # unsigned
    JwtTokens(SECRET, ttl=timedelta(seconds=-1)).issue(User("x", Role.ADMIN)),  # expired
])
def test_bad_tokens_are_rejected(client, token):
    r = client.get("/datasets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_no_token_is_rejected(client):
    assert client.get("/datasets").status_code == 401
    assert client.get("/health").status_code == 200  # the only open endpoint besides login


# --- roles ---------------------------------------------------------------------------------

ENDPOINTS = {
    "/datasets": Role.VIEWER,
    "/datasets/gdc_tcga_cases": Role.VIEWER,
    "/datasets/gdc_tcga_cases/counts?by=primary_site": Role.VIEWER,
    "/datasets/gdc_tcga_cases/rows": Role.RESEARCHER,
    "/admin/quarantine": Role.ADMIN,
    "/admin/runs": Role.ADMIN,
}


@pytest.mark.parametrize("username,role", [
    ("vera", Role.VIEWER), ("rex", Role.RESEARCHER), ("ada", Role.ADMIN)])
def test_each_role_reaches_exactly_its_endpoints(client, username, role):
    headers = auth(client, username)
    for path, needed in ENDPOINTS.items():
        expected = 200 if role.can_act_as(needed) else 403
        assert client.get(path, headers=headers).status_code == expected, (username, path)


# --- queries -------------------------------------------------------------------------------

def test_small_groups_are_suppressed(client):
    body = client.get("/datasets/gdc_tcga_cases/counts?by=primary_site",
                      headers=auth(client, "vera")).json()
    assert [g["value"] for g in body["groups"]] == ["Breast", "Lung"]
    assert body["suppressed"] == {"groups": 1, "rows": 3, "min_group_size": 10}


def test_row_filters_are_passed_through_and_paging_params_are_not_filters(client, store):
    r = client.get("/datasets/gdc_tcga_cases/rows?primary_site=Breast&limit=5&offset=10",
                   headers=auth(client, "rex"))
    assert r.status_code == 200
    assert (r.json()["limit"], r.json()["offset"]) == (5, 10)
    assert store.rows_calls == [{"primary_site": "Breast"}]


@pytest.mark.parametrize("path,code", [
    ("/datasets/no_such_table", 404),
    ("/datasets/gdc_tcga_cases;DROP TABLE x/rows", 404),
    ("/datasets/gdc_tcga_cases/rows?case_id;DROP TABLE x=1", 400),  # not a known column
    ("/datasets/gdc_tcga_cases/counts?by=diagnoses", 400),  # jsonb can't be grouped
    ("/datasets/gdc_tcga_cases/rows?limit=100000", 422),
    ("/admin/runs?dataset=nope", 404),
])
def test_unknown_names_never_reach_the_database(client, store, path, code):
    assert client.get(path, headers=auth(client, "ada")).status_code == code
    assert store.rows_calls == []


def test_short_jwt_secret_is_refused():
    with pytest.raises(ValueError):
        JwtTokens("too-short")


def test_password_hashes_are_salted_and_verify():
    a, b = HASHER.hash("pw-123456789"), HASHER.hash("pw-123456789")
    assert a != b
    assert HASHER.verify("pw-123456789", a)
    assert not HASHER.verify("pw-12345678X", a)
    assert not HASHER.verify("pw-123456789", "garbage")


# --- failure handling ----------------------------------------------------------------------

import psycopg
from psycopg_pool import PoolTimeout


@pytest.mark.parametrize("error,message", [
    (psycopg.OperationalError("connection refused"), "Database is unavailable"),
    (PoolTimeout("no free connection"), "Database is busy or unavailable"),
    (psycopg.errors.QueryCanceled("statement timeout"), "Query took too long"),
])
def test_database_problems_become_503_not_crashes(client, store, error, message):
    def broken(*_a, **_k):
        raise error
    store.rows = broken
    headers = auth(client, "rex")
    r = client.get("/datasets/gdc_tcga_cases/rows", headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"].startswith(message)
    assert r.headers["Retry-After"] == "5"
    # The process is fine and other endpoints keep working.
    assert client.get("/health").status_code == 200
    assert client.get("/datasets", headers=headers).status_code == 200


def test_ready_is_503_without_a_database(client):
    assert client.get("/ready").status_code == 503



# --- exports -------------------------------------------------------------------------------

import pyarrow as pa
import pyarrow.parquet as pq

EXPORT = "/datasets/gdc_tcga_cases/export"


def test_csv_export_streams_all_rows_with_lineage_and_filters(client, store):
    r = client.get(f"{EXPORT}.csv?primary_site=Breast", headers=auth(client, "rex"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="gdc_tcga_cases_' in r.headers["content-disposition"]
    assert r.headers["x-row-count"] == "2"
    assert store.export_filters == {"primary_site": "Breast"}

    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 2
    first = rows[0]
    assert first["case_id"] == "6e3b6b72-142d-4b8d-a462-28a205796e41"
    assert first["diagnoses"] == '[{"stage": "IA"}]'  # lists/objects become JSON text
    assert first["age_at_diagnosis_days"] == "20000"
    assert first["source_updated_at"] == "2024-01-02T00:00:00+00:00"
    assert first["_run_id"] == "run-1"  # lineage travels with the data
    assert rows[1]["diagnoses"] == ""  # missing values are empty, not "None"


def test_parquet_export_is_typed_and_carries_provenance(client):
    r = client.get(f"{EXPORT}.parquet?primary_site=Breast", headers=auth(client, "rex"))
    assert r.status_code == 200
    table = pq.read_table(io.BytesIO(r.content))
    assert table.num_rows == 2
    assert table.schema.field("age_at_diagnosis_days").type == pa.float64()
    assert table.schema.field("source_updated_at").type == pa.timestamp("us", tz="UTC")
    assert table.schema.field("case_id").type == pa.string()
    assert table.column("diagnoses").to_pylist() == ['[{"stage": "IA"}]', None]
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
    assert meta["dataset"] == "gdc_tcga_cases"
    assert meta["exported_by"] == "rex"
    assert meta["filters"] == '{"primary_site": "Breast"}'


def test_viewers_can_export_counts_but_not_rows(client):
    headers = auth(client, "vera")
    assert client.get(f"{EXPORT}.csv", headers=headers).status_code == 403
    assert client.get(f"{EXPORT}.parquet", headers=headers).status_code == 403
    r = client.get("/datasets/gdc_tcga_cases/counts.csv?by=primary_site", headers=headers)
    assert r.status_code == 200
    assert r.content.decode("utf-8-sig").splitlines() == [
        "primary_site,count", "Breast,120", "Lung,80"]
    assert (r.headers["x-suppressed-groups"], r.headers["x-suppressed-rows"]) == ("1", "3")


@pytest.mark.parametrize("path,code", [
    (f"{EXPORT}.csv?not_a_column=1", 400),
    ("/datasets/nope/export.csv", 404),
    ("/datasets/nope/export.parquet", 404),
])
def test_bad_export_requests_are_refused_before_reading(client, path, code):
    assert client.get(path, headers=auth(client, "rex")).status_code == code


def test_exports_over_the_size_cap_are_refused(client, store):
    store.matching = 10_000_000
    r = client.get(f"{EXPORT}.csv", headers=auth(client, "rex"))
    assert r.status_code == 400
    assert "Add filters" in r.json()["detail"]


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_database_down_before_export_is_a_503_not_a_broken_file(client, store, fmt):
    def down(plan):
        raise psycopg.OperationalError("connection refused")
        yield  # pragma: no cover
    store.read = down
    r = client.get(f"{EXPORT}.{fmt}", headers=auth(client, "rex"))
    assert r.status_code == 503


def test_parquet_failure_mid_read_is_a_503_not_a_broken_file(client, store):
    def dies_halfway(plan):
        yield store.export_batches[0]
        raise psycopg.OperationalError("server closed the connection")
    store.read = dies_halfway
    assert client.get(f"{EXPORT}.parquet", headers=auth(client, "rex")).status_code == 503


def test_csv_failure_mid_stream_aborts_the_download_instead_of_ending_it_cleanly(client, store):
    """Once a CSV has started, the status is already 200, so the server must cut the
    connection; the client then sees an incomplete download, not a short 'complete' file."""
    def dies_halfway(plan):
        yield store.export_batches[0]
        raise psycopg.OperationalError("server closed the connection")
    store.read = dies_halfway
    with pytest.raises(RuntimeError, match="response already started"):
        client.get(f"{EXPORT}.csv", headers=auth(client, "rex"))


# --- branding --------------------------------------------------------------------------------

def test_landing_page_is_branded_and_needs_no_database(client):
    r = client.get("/")  # the test app has no database at all
    assert r.status_code == 200
    assert "<title>OncoLake</title>" in r.text
    for ds in api.DATASETS:
        assert ds.name in r.text
    assert '/static/mark.svg' in r.text


def test_docs_page_is_branded(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "OncoLake API docs" in r.text
    assert "/static/docs.css" in r.text
    assert client.get("/openapi.json").json()["info"]["title"] == "OncoLake API"
    assert client.get("/redoc").status_code == 404  # one docs page, the branded one


@pytest.mark.parametrize("path,kind", [
    ("/static/mark.svg", "image/svg+xml"), ("/static/logo.svg", "image/svg+xml"),
    ("/static/docs.css", "text/css")])
def test_brand_assets_are_served(client, path, kind):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(kind)
