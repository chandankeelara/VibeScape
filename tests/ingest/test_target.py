"""--target local|prod (scripts/_target.py), shared by run_ingest_v2.py and
language_tags.py. Nothing here connects anywhere: prod is only ever pointed
at a temporary .env.prod naming a host that does not exist.
"""
import pytest

import _target
import language_tags
import run_ingest_v2 as runner

TOKEN = "tok-never-printed-123"


@pytest.fixture
def env(monkeypatch):
    """Snapshot the three variables so whatever apply() loads is undone."""
    for k in _target.PROD_KEYS:
        monkeypatch.setenv(k, "from-dot-env")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    return monkeypatch


def env_prod(tmp_path, **values):
    path = tmp_path / ".env.prod"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


def test_local_forces_sqlite_whatever_the_environment_says(env, db):
    env.setenv("DB_BACKEND", "turso")
    assert _target.apply("local") == f"target=local db={db}"
    import os
    assert os.environ["DB_BACKEND"] == "sqlite"


def test_prod_loads_its_own_file_over_dot_env(env, tmp_path, capsys):
    import os
    path = env_prod(tmp_path, DB_BACKEND="turso",
                    TURSO_DATABASE_URL="libsql://vibescape-test.example.invalid",
                    TURSO_AUTH_TOKEN=TOKEN)
    assert _target.apply("prod", path) == "target=prod host=vibescape-test.example.invalid"
    assert (os.environ["DB_BACKEND"], os.environ["TURSO_AUTH_TOKEN"]) == ("turso", TOKEN)
    assert TOKEN not in capsys.readouterr().out


def test_prod_without_the_file_exits_before_connecting(env, tmp_path):
    with pytest.raises(SystemExit, match=r"\.env\.prod, which does not exist"):
        _target.apply("prod", tmp_path / ".env.prod")


def test_prod_needs_all_three_keys_in_the_file_itself(env, tmp_path):
    # TURSO_AUTH_TOKEN is set in the environment (as if from .env), but a
    # token for some other database must not complete the prod config.
    path = env_prod(tmp_path, DB_BACKEND="turso", TURSO_DATABASE_URL="libsql://x.invalid")
    with pytest.raises(SystemExit, match="TURSO_AUTH_TOKEN"):
        _target.apply("prod", path)


def test_prod_must_actually_be_turso(env, tmp_path):
    path = env_prod(tmp_path, DB_BACKEND="sqlite", TURSO_DATABASE_URL="libsql://x.invalid",
                    TURSO_AUTH_TOKEN=TOKEN)
    with pytest.raises(SystemExit, match="DB_BACKEND=turso"):
        _target.apply("prod", path)


def test_both_scripts_refuse_prod_without_its_file(env):
    # conftest points _target.ENV_PROD at a file that does not exist.
    with pytest.raises(SystemExit, match="does not exist"):
        runner.main(["--target", "prod", "--recompute-only"])
    with pytest.raises(SystemExit, match="does not exist"):
        language_tags.main(["--target", "prod", "--status"])
