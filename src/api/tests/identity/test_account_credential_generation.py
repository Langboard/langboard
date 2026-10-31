"""JWT revocation generations preserve account identity without reviving credentials."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from jwt import InvalidTokenError
from jwt import encode as jwt_encode
from langboard_shared.core.security import AuthSecurity
from langboard_shared.Env import Env


@pytest.fixture
def signing_key(monkeypatch):
    key = "credential-generation-fixture-key-with-at-least-32-bytes"
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: key))
    return key


@pytest.mark.parametrize("version", [None, True, False, -1, "1", 1.5])
def test_minting_and_decoding_reject_malformed_generations(signing_key, version):
    with pytest.raises(ValueError, match="authentication version"):
        AuthSecurity.create_access_token(1, auth_version=version)
    payload = AuthSecurity.decode_access_token(AuthSecurity.create_access_token(1))
    payload["auth_version"] = version
    token = jwt_encode(payload, signing_key, algorithm=Env.JWT_ALGORITHM)
    with pytest.raises(InvalidTokenError):
        AuthSecurity.decode_access_token(token)


def test_refresh_preserves_generation_and_token_pairs_cannot_cross_it(signing_key):
    old_access, old_refresh = AuthSecurity.authenticate(1)
    fresh_access, fresh_refresh = AuthSecurity.authenticate(1, auth_version=2)
    assert AuthSecurity.decode_refresh_token(fresh_refresh)["auth_version"] == 2
    assert AuthSecurity.decode_access_token(AuthSecurity.refresh(fresh_refresh))["auth_version"] == 2
    assert AuthSecurity.decode_access_token(AuthSecurity.refresh(old_refresh))["auth_version"] == 0
    assert AuthSecurity.compare_tokens(fresh_access, fresh_refresh) is True
    assert AuthSecurity.compare_tokens(old_access, fresh_refresh) is False
    assert AuthSecurity.compare_tokens(fresh_access, old_refresh) is False


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_generation_migration_preserves_rows_and_refuses_to_erase_revocations(backend):
    schema = None
    if backend == "postgresql":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
        admin = sa.create_engine(url)
        schema = "credential_generation_" + uuid4().hex
        with admin.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        engine = sa.create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = sa.create_engine("sqlite://")
    try:
        path = Path(__file__).parents[2] / "langboard/migrations/versions/20261101000000-fabc234d5678.py"
        spec = importlib.util.spec_from_file_location("credential_generation_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        assert migration.down_revision == "efab123d4567"
        with engine.begin() as db:
            db.exec_driver_sql('CREATE TABLE "user" (id BIGINT PRIMARY KEY, email TEXT NOT NULL)')
            db.exec_driver_sql("INSERT INTO \"user\" VALUES (1, 'legacy@example.invalid')")
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
            assert db.execute(sa.text('SELECT auth_version FROM "user"')).scalar_one() == 0
            db.exec_driver_sql("INSERT INTO \"user\" (id,email) VALUES (2, 'new@example.invalid')")
            assert db.execute(sa.text('SELECT auth_version FROM "user" WHERE id=2')).scalar_one() == 0
            for value in (None, -1):
                with pytest.raises(sa.exc.IntegrityError), db.begin_nested():
                    db.execute(sa.text('UPDATE "user" SET auth_version=:value WHERE id=1'), {"value": value})
            db.exec_driver_sql('UPDATE "user" SET auth_version=1 WHERE id=1')
            with pytest.raises(RuntimeError, match="Cannot remove persisted credential revocations"):
                migration.downgrade()
            assert db.execute(sa.text('SELECT auth_version FROM "user" WHERE id=1')).scalar_one() == 1
            # Reset only disposable fixture data to exercise the no-revocation downgrade.
            db.exec_driver_sql('UPDATE "user" SET auth_version=0')
            migration.downgrade()
            assert db.execute(sa.text('SELECT email FROM "user" WHERE id=1')).scalar_one() == "legacy@example.invalid"
            migration.upgrade()
            assert db.execute(sa.text('SELECT auth_version FROM "user" WHERE id=1')).scalar_one() == 0
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as db:
                db.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
