"""The pieces that make the app run on AWS: pipeline runner, settings, user passwords, image."""

import pathlib

import dagster as dg
import pytest

from orisun_iwadi.entrypoints.api import app as api
from orisun_iwadi.entrypoints.api import users
from orisun_iwadi.entrypoints.dagster import run_job

ROOT = pathlib.Path(__file__).resolve().parents[2]


# --- run_job: the scheduled task's exit code is what raises an alert -------------------------

@dg.asset
def good():
    return 1


@dg.asset
def broken():
    raise RuntimeError("source is down")


@dg.asset(deps=[good])
def after_good():
    return 2


def _defs(*assets):
    return dg.Definitions(assets=list(assets), jobs=[dg.define_asset_job("job")])


def test_run_job_exits_0_when_every_step_succeeds():
    assert run_job.run(_defs(good, after_good), "job") == 0


def test_run_job_exits_1_when_a_step_fails_but_still_runs_the_others(caplog):
    assert run_job.run(_defs(good, broken, after_good), "job") == 1
    assert "failed steps: broken" in caplog.text  # only the broken one


def test_run_job_knows_the_real_job():
    from orisun_iwadi.entrypoints.dagster.definitions import defs
    assert defs.resolve_job_def("ingest_all")


# --- API settings ------------------------------------------------------------------------------

def test_api_pool_size_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("API_POSTGRES_DSN", "host=db")
    assert api.Settings.from_env().pool_size == 10
    monkeypatch.setenv("API_DB_POOL_SIZE", "5")
    assert api.Settings.from_env().pool_size == 5
    monkeypatch.setenv("API_DB_POOL_SIZE", "0")
    with pytest.raises(RuntimeError, match="API_DB_POOL_SIZE"):
        api.Settings.from_env()


# --- user CLI: on AWS the password comes from Secrets Manager ----------------------------------

class FakeSecrets:
    def __init__(self):
        self.asked = []

    def get_secret_value(self, SecretId):
        self.asked.append(SecretId)
        return {"SecretString": "from-secrets-manager"}


def test_password_is_read_from_secrets_manager_when_a_secret_is_given(monkeypatch):
    fake = FakeSecrets()
    monkeypatch.setattr(users.boto3, "client", lambda service: fake)
    assert users.read_password("oncolake/users/alice") == "from-secrets-manager"
    assert fake.asked == ["oncolake/users/alice"]


def test_password_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setattr(users.boto3, "client", lambda service: pytest.fail("no AWS call"))
    monkeypatch.setenv("ORISUN_PASSWORD", "from-env-123")
    assert users.read_password(None) == "from-env-123"


# --- container image ---------------------------------------------------------------------------

def test_image_runs_as_non_root_and_ships_no_secrets():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER 10001" in dockerfile
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert {".env", ".git", ".venv"} <= set(ignored)


def test_image_role_setup_is_the_same_sql_as_locally():
    sql = (ROOT / "local/db-roles.sql").read_text()
    assert "COPY local/db-roles.sql /app/db/db-roles.sql" in (ROOT / "Dockerfile").read_text()
    # On RDS the admin isn't a superuser; it needs this to hand schemas to orisun_etl.
    assert "GRANT orisun_etl TO CURRENT_USER" in sql
