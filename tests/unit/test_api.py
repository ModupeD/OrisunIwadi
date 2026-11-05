"""The API with in-memory fakes: role checks, tokens, validation. No database needed."""

from datetime import timedelta

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


@pytest.fixture
def store():
    return FakeReadStore()


@pytest.fixture
def client(store):
    app = api.create_app(api.Settings(dsn=None, jwt_secret=SECRET))
    app.dependency_overrides[api.get_queries] = lambda: DatasetQueries(api.DATASETS, store)
    authenticate = Authenticate(FakeUsers(), HASHER)
    app.dependency_overrides[api.get_authenticate] = lambda: authenticate
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
