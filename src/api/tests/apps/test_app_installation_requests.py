"""Native durable approval: exact artifacts, current authority and separate board grants."""

import importlib.util
from hashlib import sha256
from pathlib import Path
import langboard.routes.settings.AppPackageSettingsApi  # noqa: F401
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.apps import AppInstallationRequests as packages
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import AppDefinition, AppInstallationRequest, BoardAppBinding, User
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, save_policy
from langboard_shared.domain.services.AppInstallationRequests import get_request, list_requests, review_request
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, AppRegistryDenied
from langboard_shared.Env import Env
from sqlalchemy import inspect
from test_app_package import DECLARATION, bundle


@pytest.fixture
def setup_requests(board, monkeypatch, tmp_path):
    engine = DbEngine.get_main_engine()
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024150000-bdef234a5678.py"
    spec = importlib.util.spec_from_file_location("installation_requests_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    from langboard_shared.publishers import AppSettingPublisher

    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    monkeypatch.setattr(type(Env), "API_URL", property(lambda _: "https://api.example.invalid"))
    monkeypatch.setattr(type(Env), "PUBLIC_UI_URL", property(lambda _: "https://host.example.invalid"))
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "files"))
    with DbSession.atomic() as db:
        admin = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
        admin.is_admin = True
        db.update(admin)
    return board[1], admin, migration


def assert_no_install():
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppDefinition)).all() == []
        assert db.exec(SqlBuilder.select.table(BoardAppBinding)).all() == []


def test_manual_request_review_replay_and_current_access(setup_requests):
    actor, admin, _ = setup_requests
    body = bundle()
    request = packages.submit_request(actor, "manual-identity", body=body)
    assert request["decision"] == "pending" and request["installation_status"] == "not_started"
    assert request["source"] == "manual" and "storage" not in str(request)
    assert_no_install()
    assert packages.submit_request(actor, "manual-identity", body=body) == request
    with pytest.raises(AppRegistryConflict):
        packages.submit_request(actor, "manual-identity", body=bundle(declaration={**DECLARATION, "version": "1.1.0"}))
    with pytest.raises(AppRegistryDenied):
        review_request(
            actor,
            request["uid"],
            "approve",
            request["revision"],
            "",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
    installed = review_request(
        admin,
        request["uid"],
        "approve",
        request["revision"],
        "reviewed",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert installed["decision"] == "approved" and installed["installation_status"] == "installed"
    assert installed["app"]["installation"]["sha256"] == sha256(body).hexdigest()
    assert (
        review_request(
            admin,
            request["uid"],
            "approve",
            request["revision"],
            "reviewed",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
        == installed
    )
    with pytest.raises(AppRegistryConflict):
        review_request(
            admin,
            request["uid"],
            "reject",
            installed["revision"],
            "reviewed",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(BoardAppBinding)).all() == []
        admin.is_admin = False
        db.update(admin)
    with pytest.raises(AppRegistryDenied):
        get_request(admin, request["uid"])
    assert list_requests(admin)["items"] == []
    with pytest.raises(AppRegistryDenied):
        review_request(
            admin,
            request["uid"],
            "approve",
            request["revision"],
            "reviewed",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )


def test_official_request_binds_release_before_approval(setup_requests, monkeypatch):
    actor, admin, _ = setup_requests
    body = bundle()
    calls = []

    def download(key, tag):
        calls.append((key, tag))
        return body, {
            "kind": "official",
            "repository": "yamonco/langboard-app-github",
            "tag": "v1.0.0",
            "asset_id": 42,
            "sha256": sha256(body).hexdigest(),
        }

    monkeypatch.setattr(packages, "download_official_package", download)
    request = packages.submit_request(actor, "official-identity", key="github-panel")
    assert_no_install()
    assert packages.submit_request(actor, "official-identity", key="github-panel") == request
    assert calls == [("github-panel", None)]
    with pytest.raises(AppRegistryConflict):
        packages.submit_request(actor, "official-identity", key="github-panel", tag="v1.0.0")
    monkeypatch.setattr(
        packages, "download_official_package", lambda *args: pytest.fail("Approval must not resolve release again")
    )
    result = review_request(
        admin,
        request["uid"],
        "approve",
        request["revision"],
        "",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert result["installation_status"] == "installed"
    assert result["app"]["installation"]["tag"] == "v1.0.0"


def test_failed_install_rejection_policy_revocation_and_staging_validation(setup_requests):
    actor, admin, _ = setup_requests
    for body in (b"invalid", bundle([("../escape", "unsafe")])):
        with pytest.raises(ValueError):
            packages.submit_request(actor, "invalid-package", body=body)
    request = packages.submit_request(actor, "failed-identity", body=bundle(), expected_revision="a" * 64)
    failed = review_request(
        admin,
        request["uid"],
        "approve",
        request["revision"],
        "",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert failed["decision"] == "approved" and failed["installation_status"] == "failed"
    assert failed["installation_error"] == "app_revision_conflict" and failed["app"] is None
    assert_no_install()
    rejected = packages.submit_request(actor, "reject-identity", body=bundle())
    result = review_request(
        admin,
        rejected["uid"],
        "reject",
        rejected["revision"],
        "Declined",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert result["decision"] == "rejected" and result["installation_status"] == "not_started"
    pending = packages.submit_request(actor, "policy-identity", body=bundle())
    from langboard_shared.domain.services.AppGovernance import get_policy

    save_policy(admin, "disabled", get_policy(admin)["revision"])
    with pytest.raises(AppGovernanceDenied):
        packages.submit_request(actor, "policy-identity", body=bundle())
    with pytest.raises(AppGovernanceDenied):
        review_request(
            admin,
            pending["uid"],
            "approve",
            pending["revision"],
            "",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
    assert get_request(actor, pending["uid"])["decision"] == "pending"
    assert_no_install()


def test_installation_savepoint_rolls_back_partial_writes(setup_requests):
    actor, admin, _ = setup_requests
    request = packages.submit_request(actor, "rollback-identity", body=bundle())

    def failing_installer(*args):
        packages.commit_requested_package(*args)
        raise ValueError("Failure after registry write")

    result = review_request(
        admin, request["uid"], "approve", request["revision"], "", failing_installer, packages.prepare_requested_package
    )
    assert result["decision"] == "approved" and result["installation_status"] == "failed"
    assert_no_install()


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_authenticated_http_submission_admin_review_and_own_visibility(setup_requests, monkeypatch):
    actor, admin, _ = setup_requests
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        response = client.post(
            "/apps/installation-requests/upload",
            files={"file": ("app.zip", bundle())},
            data={"request_key": "http-identity"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        request = response.json()["request"]
        assert client.get("/apps/installation-requests", headers=headers).json()["items"][0]["uid"] == request["uid"]
        review = {"decision": "approve", "expected_revision": request["revision"]}
        assert (
            client.post(
                f"/settings/apps/installation-requests/{request['uid']}/review", json=review, headers=headers
            ).status_code
            == 403
        )
        access, refresh = AuthSecurity.authenticate(admin.id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.post(
            f"/settings/apps/installation-requests/{request['uid']}/review",
            json=review,
            headers={"Authorization": f"Bearer {access}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["request"]["installation_status"] == "installed"


def test_migration_matches_model_and_retains_receipts(setup_requests):
    actor, _, migration = setup_requests
    engine = DbEngine.get_main_engine()
    assert migration.down_revision == "acde123f4567"
    assert {column["name"] for column in inspect(engine).get_columns("app_installation_request")} == set(
        AppInstallationRequest.__table__.columns.keys()
    )
    packages.submit_request(actor, "retained-identity", body=bundle())
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="retained"):
                migration.downgrade()


def test_staging_io_runs_outside_transaction_and_rechecks_revocation(setup_requests, monkeypatch):
    actor, admin, _ = setup_requests
    from langboard_shared.core.storage import Storage

    original_upload = Storage.upload_named

    def upload(*args):
        assert not DbSession.has_active_transaction()
        return original_upload(*args)

    monkeypatch.setattr(Storage, "upload_named", upload)

    def download(*args):
        assert not DbSession.has_active_transaction()
        with DbSession.atomic() as db:
            actor.activated_at = None
            db.update(actor)
        return bundle(), {"kind": "official", "tag": "v1.0.0"}

    monkeypatch.setattr(packages, "download_official_package", download)
    with pytest.raises(AppGovernanceDenied):
        packages.submit_request(actor, "revoked-during-download", key="github-panel")
    assert list_requests(admin)["items"] == []
    assert_no_install()


def test_board_context_rechecked_before_approval(setup_requests, board):
    actor, admin, _ = setup_requests
    role = board[4]
    with DbSession.atomic() as db:
        role.actions = ["read", "update"]
        db.update(role)
    request = packages.submit_request(actor, "board-context-request", body=bundle(), project_uid=board[2].get_uid())
    with DbSession.atomic() as db:
        role.actions = ["read"]
        db.update(role)
    with pytest.raises(AppGovernanceDenied):
        review_request(
            admin,
            request["uid"],
            "approve",
            request["revision"],
            "",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
    assert_no_install()


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_concurrent_submission_and_review_install_once(setup_requests):
    from concurrent.futures import ThreadPoolExecutor

    actor, admin, _ = setup_requests
    body = bundle()
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: packages.submit_request(actor, "concurrent-request", body=body), range(4)))
    assert len({row["uid"] for row in receipts}) == 1
    request = receipts[0]
    calls = []

    def installer(*args):
        calls.append(True)
        return packages.commit_requested_package(*args)

    with ThreadPoolExecutor(max_workers=4) as pool:
        reviews = list(
            pool.map(
                lambda _: review_request(
                    admin,
                    request["uid"],
                    "approve",
                    request["revision"],
                    "",
                    installer,
                    packages.prepare_requested_package,
                ),
                range(4),
            )
        )
    assert len(calls) == 1
    assert all(row["installation_status"] == "installed" for row in reviews)
    assert len(list_requests(admin)["items"]) == 1


def test_review_preparation_io_outside_transaction_and_final_admin_revocation(setup_requests, monkeypatch):
    actor, admin, _ = setup_requests
    request = packages.submit_request(actor, "review-io-boundary", body=bundle())
    from langboard_shared.core.storage import Storage

    original_get = Storage.get_file
    original_upload = Storage.upload_named

    def read(*args):
        assert not DbSession.has_active_transaction()
        return original_get(*args)

    def upload(*args):
        assert not DbSession.has_active_transaction()
        return original_upload(*args)

    monkeypatch.setattr(Storage, "get_file", read)
    monkeypatch.setattr(Storage, "upload_named", upload)

    def prepare(*args):
        result = packages.prepare_requested_package(*args)
        with DbSession.atomic() as db:
            admin.is_admin = False
            db.update(admin)
        return result

    with pytest.raises(AppRegistryDenied):
        review_request(
            admin, request["uid"], "approve", request["revision"], "", packages.commit_requested_package, prepare
        )
    assert get_request(actor, request["uid"])["decision"] == "pending"
    assert_no_install()


def test_unexpected_install_failure_rolls_back_decision_and_allows_safe_retry(setup_requests):
    actor, admin, _ = setup_requests
    request = packages.submit_request(actor, "uncertain-review-retry", body=bundle())

    def interrupted(*args):
        packages.commit_requested_package(*args)
        raise RuntimeError("Interrupted before receipt commit")

    with pytest.raises(RuntimeError, match="Interrupted"):
        review_request(
            admin, request["uid"], "approve", request["revision"], "", interrupted, packages.prepare_requested_package
        )
    assert_no_install()
    reread = get_request(actor, request["uid"])
    assert reread["decision"] == "pending" and reread["installation_status"] == "not_started"
    result = review_request(
        admin,
        request["uid"],
        "approve",
        request["revision"],
        "",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert result["installation_status"] == "installed"


def test_staged_artifact_integrity_failure_has_separate_approved_failed_receipt(setup_requests, monkeypatch):
    actor, admin, _ = setup_requests
    request = packages.submit_request(actor, "tampered-artifact", body=bundle())
    from langboard_shared.core.storage import Storage

    monkeypatch.setattr(Storage, "get_file", lambda _: b"changed staged bytes")
    result = review_request(
        admin,
        request["uid"],
        "approve",
        request["revision"],
        "",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert result["decision"] == "approved" and result["installation_status"] == "failed"
    assert result["installation_error"] == "package_install_failed"
    assert_no_install()
    monkeypatch.setattr(Storage, "get_file", lambda _: pytest.fail("Failed review replay must not retry install"))
    assert (
        review_request(
            admin,
            request["uid"],
            "approve",
            request["revision"],
            "",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
        == result
    )


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_postgresql_timestamp_roundtrip_preserves_instant_in_non_utc_session(setup_requests):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update

    actor, admin, _ = setup_requests
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql("SET TIME ZONE 'Asia/Seoul'")
    before = datetime.now(timezone.utc)
    body = bundle()
    first = packages.submit_request(actor, "timezone-roundtrip", body=body)
    after = datetime.now(timezone.utc)
    assert before <= datetime.fromisoformat(first["created_at"]) <= after
    assert packages.submit_request(actor, "timezone-roundtrip", body=body) == first

    # Deliberately persist a known +09:00 timestamp, then read through the real
    # native ORM and serializer. The represented instant must remain unchanged.
    known = datetime(2026, 10, 24, 20, 0, 0, 123456, tzinfo=timezone(timedelta(hours=9)))
    with DbSession.atomic() as db:
        db.exec(
            update(AppInstallationRequest)
            .where(
                AppInstallationRequest.requester_id == actor.id,
                AppInstallationRequest.request_key == "timezone-roundtrip",
            )
            .values(created_at=known, updated_at=known)
        )
    reread = get_request(actor, first["uid"])
    expected = "2026-10-24T11:00:00.123456+00:00"
    assert reread["created_at"] == expected and reread["updated_at"] == expected
    assert datetime.fromisoformat(reread["created_at"]).timestamp() == known.timestamp()
    approved = review_request(
        admin,
        first["uid"],
        "approve",
        first["revision"],
        "",
        packages.commit_requested_package,
        packages.prepare_requested_package,
    )
    assert approved["created_at"] == expected
    assert (
        review_request(
            admin,
            first["uid"],
            "approve",
            first["revision"],
            "",
            packages.commit_requested_package,
            packages.prepare_requested_package,
        )
        == approved
    )
