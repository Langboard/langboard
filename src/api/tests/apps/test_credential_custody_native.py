"""Secret-free durable custody and fixed callback lifecycle."""

import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from test_notification_destination_native import NotificationDestinationNativeTest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.core.security import KeyVault
from langboard_shared.domain.models import AppConnectionCredential
from langboard_shared.domain.models.AppConnectionCredentialCustody import AppConnectionCredentialCustody, AppConnectionCredentialHandoff
from langboard_shared.domain.services.AppConnectionAuthentication import authenticate_connection_credential
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied

custody = importlib.import_module("langboard_shared.domain.services.AppCredentialCustody")
webhooks = importlib.import_module("langboard_shared.tasks.webhooks.WebhookTask")
utils = importlib.import_module("langboard_shared.tasks.webhooks.utils")


class CredentialCustodyNativeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        NotificationDestinationNativeTest.setUp(self)
        for model in (AppConnectionCredentialCustody, AppConnectionCredentialHandoff):
            model.__table__.create(self.engine)
        self.destination = SimpleNamespace(id=SnowflakeID(), revision=1)
        self.setting = SimpleNamespace(url="https://approved.example/events", secret_id="test-signing-ref")
        target = SimpleNamespace(url="https://203.0.113.1/events", host_header="approved.example", sni_hostname="approved.example")
        for mock in (
            patch.object(custody, "current_destination", return_value=(self.destination, self.setting)),
            patch.object(KeyVault, "get_key", return_value="test-service-signing-secret"),
            patch.object(utils, "ensure_public_webhook_url", new=AsyncMock(return_value=target)),
        ):
            mock.start()
            self.addCleanup(mock.stop)
        self.received = []

        async def receiver(target, body, headers, **kwargs):
            from langboard_sdk.webhooks import verify_webhook
            verify_webhook(body, headers, "test-service-signing-secret")
            payload = json.loads(body)
            with self.assertRaises(AppGovernanceDenied):
                authenticate_connection_credential(payload["credential"]["token"])
            staged = custody.read_custodied_credential_status(payload["credential"]["token"])
            self.assertEqual((staged["state"], staged["usable"]), ("prepared", False))
            self.received.append(payload)

        self.send = AsyncMock(side_effect=receiver)
        sender = patch.object(webhooks, "post_resolved_webhook_bytes", new=self.send)
        sender.start()
        self.addCleanup(sender.stop)
        self.grant = custody.approve_credential_custody(self.owner, self.connection.id,
            expected_connection_revision=self.connection.edit_revision(), expected_destination_revision=1)
        self.identifier = SnowflakeID.from_short_code(self.grant["custody_uid"])

    async def handoff(self, request_id="first"):
        return await custody.handoff_connection_credential(self.owner, self.identifier,
            expected_revision=1, request_id=request_id)

    async def test_fixed_signed_callback_and_metadata_only_receipt(self):
        result = await self.handoff()
        self.assertEqual(result["state"], "delivered")
        self.assertNotIn("token", result)
        self.assertEqual(len(self.received), 1)
        payload = self.received[0]
        token = payload["credential"]["token"]
        principal = authenticate_connection_credential(token)
        self.assertTrue(custody.read_custodied_credential_status(token)["usable"])
        self.assertEqual(principal.connection_id, self.connection.id)
        with DbSession.atomic() as db:
            credentials = db.exec(SqlBuilder.select.table(AppConnectionCredential)).all()
            receipts = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff)).all()
            self.assertNotIn(token, str([row.model_dump() for row in credentials + receipts]))
            custody.validate_custodied_credential(db, next(row for row in credentials if row.id == principal.credential_id))
        self.assertEqual(await self.handoff(), result)
        self.assertEqual(self.send.await_count, 1)

    async def test_unknown_callback_revokes_credential_and_does_not_replay(self):
        self.send.side_effect = TimeoutError("provider response lost")
        with self.assertRaises(custody.CredentialHandoffUnknown):
            await self.handoff()
        replay = await self.handoff()
        self.assertEqual(replay["state"], "unknown_revoked")
        self.assertEqual(self.send.await_count, 1)
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff)).first()
            credential = db.exec(SqlBuilder.select.table(AppConnectionCredential).where(AppConnectionCredential.id == row.credential_id)).first()
            self.assertIsNotNone(credential.revoked_at)
            with self.assertRaises(AppGovernanceDenied):
                custody.validate_custodied_credential(db, credential)

    async def test_other_actor_and_changed_destination_cannot_issue(self):
        with self.assertRaises(AppGovernanceDenied):
            await custody.handoff_connection_credential(self.other, self.identifier, expected_revision=1, request_id="other")
        self.destination.revision = 2
        with self.assertRaises(AppGovernanceConflict):
            await self.handoff()
        self.assertEqual(self.send.await_count, 0)

    async def test_revoke_custody_revokes_only_its_issued_credentials(self):
        await self.handoff()
        token = self.received[0]["credential"]["token"]
        result = custody.revoke_credential_custody(self.owner, self.identifier, expected_revision=1)
        self.assertEqual((result["state"], result["revision"]), ("revoked", 2))
        with self.assertRaises(AppGovernanceDenied):
            authenticate_connection_credential(token)
        self.assertFalse(custody.read_custodied_credential_status(token)["usable"])
        self.assertEqual(authenticate_connection_credential(self.token["token"]).connection_id, self.connection.id)

    async def test_renewal_uses_same_callback_and_bounded_existing_lifetime(self):
        await self.handoff()
        original = self.received[0]["credential"]["token"]
        with self.assertRaises(AppGovernanceConflict):
            await custody.renew_custodied_credential(original, request_id="renew-1")
        principal = authenticate_connection_credential(original)
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppConnectionCredential).where(AppConnectionCredential.id == principal.credential_id)).first()
            row.expires_at = SafeDateTime.now() + timedelta(seconds=600)
            db.update(row)
        with self.assertRaises(AppGovernanceConflict):
            await custody.renew_custodied_credential(original, request_id="first")
        await self.handoff("already-used")
        with self.assertRaises(AppGovernanceConflict):
            await custody.renew_custodied_credential(original, request_id="already-used")
        renewed = await custody.renew_custodied_credential(original, request_id="renew-1")
        self.assertEqual(renewed["state"], "delivered")
        self.assertEqual(len(self.received), 3)
        with self.assertRaises(AppGovernanceDenied):
            authenticate_connection_credential(original)
        self.assertEqual(authenticate_connection_credential(self.received[-1]["credential"]["token"]).connection_id, self.connection.id)

    async def test_native_http_custody_user_approval_and_staged_metadata(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppCredentialCustodyApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        with TestClient(app) as client:
            approve_path = f"/settings/apps/connections/{self.connection.get_uid()}/credential-custody"
            body = {"expected_connection_revision": self.connection.edit_revision(),
                    "expected_destination_revision": 1, "expected_revision": 1}
            self.assertEqual(client.post(approve_path, json=body).status_code, 401)
            self.assertEqual(client.post(approve_path, json=body,
                headers={"Authorization": "Bearer " + self.token["token"]}).status_code, 401)
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            approved = client.post(approve_path, json=body, headers=headers)
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["revision"], 2)
            delivered = client.post(f"/settings/apps/credential-custody/{self.grant['custody_uid']}/handoff",
                json={"expected_revision": 2, "request_id": "http-handoff"}, headers=headers)
            self.assertEqual(delivered.status_code, 200, delivered.text)
            self.assertEqual(delivered.headers["cache-control"], "no-store")
            self.assertEqual(delivered.json()["state"], "delivered")
            token = self.received[0]["credential"]["token"]
            self.assertNotIn(token, delivered.text)
            status = client.get("/apps/v1/credential-custody/status", headers={"Authorization": "Bearer " + token})
            self.assertEqual(status.status_code, 200, status.text)
            self.assertTrue(status.json()["usable"])
            self.assertNotIn(token, status.text)
            self.assertEqual(client.get("/apps/v1/credential-custody/status", headers=headers).status_code, 403)
            revoked = client.post(f"/settings/apps/credential-custody/{self.grant['custody_uid']}/revoke",
                json={"expected_revision": 2}, headers=headers)
            self.assertEqual(revoked.status_code, 200, revoked.text)
            status = client.get("/apps/v1/credential-custody/status", headers={"Authorization": "Bearer " + token})
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json()["usable"])

    async def test_native_http_unknown_handoff_is_503_and_revoked_metadata(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppCredentialCustodyApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        self.send.side_effect = TimeoutError("secret-bearing provider callback response lost")
        with TestClient(app) as client:
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            path = f"/settings/apps/credential-custody/{self.grant['custody_uid']}/handoff"
            body = {"expected_revision": 1, "request_id": "http-unknown"}
            response = client.post(path, json=body, headers=headers)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("secret-bearing", response.text)
            repeated = client.post(path, json=body, headers=headers)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json()["state"], "unknown_revoked")
            self.assertEqual(self.send.await_count, 1)

    async def test_custody_migration_preserves_existing_credentials_and_history(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect, text
        source = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261116212209-bd0e06d55625.py"
        spec = importlib.util.spec_from_file_location("custody_schema_migration", source)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE app_connection_credential (id BIGINT PRIMARY KEY, "
                "connection_id BIGINT NOT NULL, issued_by BIGINT NOT NULL, token_hash TEXT NOT NULL, "
                "identity_hash TEXT NOT NULL, expires_at DATETIME NOT NULL, revoked_at DATETIME, "
                "created_at DATETIME, updated_at DATETIME)"))
            connection.execute(text("INSERT INTO app_connection_credential "
                "(id,connection_id,issued_by,token_hash,identity_hash,expires_at) "
                "VALUES (1,2,3,'retained-hash','retained-identity','2030-01-01')"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            self.assertIn("custody_id", {column["name"] for column in inspect(connection).get_columns("app_connection_credential")})
            self.assertEqual(connection.execute(text("SELECT token_hash FROM app_connection_credential WHERE id=1")).scalar_one(), "retained-hash")
            migration.downgrade()
            self.assertNotIn("custody_id", {column["name"] for column in inspect(connection).get_columns("app_connection_credential")})
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_connection_credential")).scalar_one(), 1)
            migration.upgrade()
            connection.execute(text("INSERT INTO app_connection_credential_custody "
                "(id,connection_id,approved_by,identity_hash,destination_id,destination_revision,state,revision) "
                "VALUES (10,2,3,'retained-identity',4,1,'enabled',1)"))
            with self.assertRaisesRegex(RuntimeError, "retained"):
                migration.downgrade()
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_connection_credential_custody")).scalar_one(), 1)

    async def test_real_registered_destination_is_pinned_and_change_denies_authentication(self):
        from langboard_shared.domain.models import AppEventDestination, WebhookSetting
        from langboard_shared.domain.services.AppEventDestination import bind_app_event_destination, current_destination
        for model in (AppEventDestination, WebhookSetting):
            model.__table__.create(self.engine)
        with DbSession.atomic() as db:
            declaration = self.definition.declaration
            self.definition.declaration = {**declaration, "trust": {
                **declaration["trust"], "data_origins": ["https://approved.example"]}}
            db.update(self.definition)
            setting = WebhookSetting(name="Approved service", url="https://approved.example/events",
                secret_id="test-signing-ref", events=[custody.HANDOFF_EVENT])
            db.insert(setting)
        bind_app_event_destination(self.other, self.connection.id, setting.id, None)
        with patch.object(custody, "current_destination", new=current_destination):
            grant = custody.approve_credential_custody(self.owner, self.connection.id,
                expected_connection_revision=self.connection.edit_revision(), expected_destination_revision=1, expected_revision=1)
            result = await custody.handoff_connection_credential(self.owner, self.identifier,
                expected_revision=grant["revision"], request_id="real-destination")
            self.assertEqual(result["state"], "delivered")
            token = self.received[0]["credential"]["token"]
            self.assertTrue(custody.read_custodied_credential_status(token)["usable"])
            with DbSession.atomic() as db:
                setting.url = "https://approved.example/changed"
                db.update(setting)
            self.assertFalse(custody.read_custodied_credential_status(token)["usable"])
            with self.assertRaises(AppGovernanceDenied):
                authenticate_connection_credential(token)
