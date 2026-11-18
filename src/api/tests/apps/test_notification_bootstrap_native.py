"""User-started proof context travels only through the approved signed handoff."""

import unittest
from datetime import timedelta
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import AppConnection, AppConnectionCredential, EmployeeMembershipPolicy, Project, ProjectAssignedUser, ProjectRole
from langboard_shared.domain.models.AppConnectionCredentialCustody import AppConnectionCredentialHandoff
from langboard_shared.domain.models.AppNotificationDestination import AppNotificationDestinationChallenge
from langboard_shared.domain.services import AppCredentialCustody as custody
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, AppGovernanceConflict
from langboard_shared.domain.services.AppNotificationDestinations import (
    create_destination_challenge, submit_destination_proof, approve_destination_challenge, cancel_destination_challenge,
    read_shared_challenge_scope,
)
from test_notification_destination_native import IDENTITY, DESTINATION
from test_credential_custody_native import CredentialCustodyNativeTest


class NotificationBootstrapNativeTest(unittest.IsolatedAsyncioTestCase):
    connection_revision = CredentialCustodyNativeTest.connection_revision

    def setUp(self):
        CredentialCustodyNativeTest.setUp(self)

    def challenge(self, request_id="bootstrap"):
        return create_destination_challenge(self.owner, self.connection.id, request_id=request_id,
            expected_connection_revision=self.connection_revision())

    async def bootstrap(self, challenge, request_id="bootstrap-handoff", actor=None, **overrides):
        return await custody.handoff_connection_credential(actor or self.owner, self.identifier,
            expected_revision=1, request_id=request_id,
            **{"notification_challenge_id": SnowflakeID.from_short_code(challenge["challenge_uid"]),
               "notification_challenge_token": challenge["challenge_token"], **overrides})

    async def test_signed_context_binds_exact_user_connection_and_challenge_without_durable_nonce(self):
        challenge = self.challenge()
        result = await self.bootstrap(challenge)
        self.assertEqual(result["state"], "delivered")
        context = self.received[0]["destination_challenge"]
        self.assertEqual(context, {"challenge_uid": challenge["challenge_uid"],
            "challenge_token": challenge["challenge_token"], "expires_at": challenge["expires_at"],
            "initiated_by_user_uid": self.owner.get_uid()})
        self.assertEqual(self.received[0]["connection_uid"], self.connection.get_uid())
        self.assertNotIn(challenge["challenge_token"], str(result))
        with DbSession.atomic() as db:
            handoff = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff)).first()
            self.assertEqual(handoff.notification_challenge_id, SnowflakeID.from_short_code(challenge["challenge_uid"]))
            self.assertNotIn(challenge["challenge_token"], str(handoff.model_dump()))
            stored = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).first()
            self.assertNotIn(challenge["challenge_token"], str(stored.model_dump()))
        # Prepared tokens were rejected inside the signed receiver. Only after
        # native delivered/usable confirmation can this exact credential attest.
        token = self.received[0]["credential"]["token"]
        self.assertTrue(custody.read_custodied_credential_status(token)["usable"])
        proof = submit_destination_proof(token, SnowflakeID.from_short_code(challenge["challenge_uid"]),
            challenge_token=challenge["challenge_token"], request_id="external-proof",
            external_identity=IDENTITY, destination=DESTINATION)
        self.assertEqual(proof["state"], "awaiting_user_approval")
        approved = approve_destination_challenge(self.owner, SnowflakeID.from_short_code(challenge["challenge_uid"]),
            expected_revision=proof["revision"], proof_id=SnowflakeID.from_short_code(proof["proof_uid"]))
        self.assertEqual(approved["state"], "verified")

    async def test_shared_handoff_carries_only_selected_board_and_current_app_can_read_it(self):
        for model in (EmployeeMembershipPolicy, Project, ProjectAssignedUser, ProjectRole):
            model.__table__.create(self.engine, checkfirst=True)
        with DbSession.atomic() as db:
            board = Project(owner_id=self.owner.id, title="Selected board")
            db.insert(board)
        challenge = create_destination_challenge(self.owner, self.connection.id, request_id="shared-scope",
            expected_connection_revision=self.connection_revision(), kind="shared", project_uid=board.get_uid())
        result = await self.bootstrap(challenge)
        self.assertEqual(result["state"], "delivered")
        self.assertNotIn("project_uid", result)
        signed = self.received[0]["destination_challenge"]
        self.assertEqual(signed["project_uid"], board.get_uid())
        self.assertEqual(signed["kind"], "shared")
        scope = read_shared_challenge_scope(self.received[0]["credential"]["token"],
            SnowflakeID.from_short_code(signed["challenge_uid"]), challenge_token=signed["challenge_token"])
        self.assertEqual(scope, {"challenge_uid": signed["challenge_uid"], "connection_uid": self.connection.get_uid(),
                                 "project_uid": board.get_uid(), "expires_at": signed["expires_at"]})

    async def test_other_user_connection_wrong_nonce_and_partial_context_never_issue_or_send(self):
        challenge = self.challenge()
        with DbSession.atomic() as db:
            before = len(db.exec(SqlBuilder.select.table(AppConnectionCredential)).all())
            connection = AppConnection(app_key=self.connection.app_key, owner_id=self.other.id, state="connected")
            db.insert(connection)
        with self.assertRaises(AppGovernanceDenied): await self.bootstrap(challenge, actor=self.other)
        with self.assertRaises(AppGovernanceDenied): custody.read_credential_custody(self.other, self.connection.id)
        with self.assertRaises(AppGovernanceDenied):
            await self.bootstrap(challenge, notification_challenge_token="X" * 43)
        other_challenge = create_destination_challenge(self.other, connection.id, request_id="other",
            expected_connection_revision=self.other_connection_revision(connection))
        with self.assertRaises(AppGovernanceDenied): await self.bootstrap(other_challenge)
        with self.assertRaises(ValueError): await self.bootstrap(challenge, notification_challenge_token=None)
        with self.assertRaises(ValueError): await self.bootstrap(challenge, notification_challenge_id=None)
        self.send.assert_not_awaited()
        with DbSession.atomic() as db:
            self.assertEqual(len(db.exec(SqlBuilder.select.table(AppConnectionCredential)).all()), before)

    def other_connection_revision(self, connection):
        from langboard_shared.domain.services.AppConnectionManagement import list_inbound_connections
        rows = list_inbound_connections(self.other, connection.app_key)["items"]
        return next(row["revision"] for row in rows if row["connection_uid"] == connection.get_uid())

    async def test_expired_or_already_attested_challenge_does_not_become_a_bootstrap(self):
        challenge = self.challenge()
        with DbSession.atomic() as db:
            stored = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).first()
            expiry = stored.expires_at
            stored.expires_at = SafeDateTime.now() - timedelta(seconds=1); db.update(stored)
        with self.assertRaises(AppGovernanceConflict): await self.bootstrap(challenge)
        with DbSession.atomic() as db:
            stored.expires_at = expiry; db.update(stored)
        submit_destination_proof(self.token["token"], SnowflakeID.from_short_code(challenge["challenge_uid"]),
            challenge_token=challenge["challenge_token"], request_id="existing-proof",
            external_identity=IDENTITY, destination=DESTINATION)
        with self.assertRaises(AppGovernanceConflict): await self.bootstrap(challenge)
        self.send.assert_not_awaited()

    async def test_same_request_replays_metadata_but_cannot_change_or_reuse_its_context(self):
        challenge = self.challenge()
        delivered = await self.bootstrap(challenge)
        self.assertEqual(await self.bootstrap(challenge), delivered)
        self.send.assert_awaited_once()
        with self.assertRaises(AppGovernanceConflict): await self.bootstrap(challenge, request_id="another-handoff")
        second = self.challenge("second-challenge")
        with self.assertRaises(AppGovernanceConflict): await self.bootstrap(second)
        with self.assertRaises(AppGovernanceConflict):
            await custody.handoff_connection_credential(self.owner, self.identifier,
                expected_revision=1, request_id="bootstrap-handoff")
        with DbSession.atomic() as db:
            stored = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
                AppNotificationDestinationChallenge.id == SnowflakeID.from_short_code(challenge["challenge_uid"]))).first()
            stored.expires_at = SafeDateTime.now() - timedelta(seconds=1); db.update(stored)
        # A retained receipt is inspectable after expiry, without another send.
        self.assertEqual(await self.bootstrap(challenge), delivered)
        self.send.assert_awaited_once()

    async def test_cancel_during_callback_revokes_staged_credential_before_usability(self):
        challenge = self.challenge()
        receiver = self.send.side_effect
        async def cancel_after_staging(*args, **kwargs):
            await receiver(*args, **kwargs)
            cancel_destination_challenge(self.owner, SnowflakeID.from_short_code(challenge["challenge_uid"]), expected_revision=1)
        self.send.side_effect = cancel_after_staging
        result = await self.bootstrap(challenge)
        self.assertEqual(result["state"], "revoked")
        token = self.received[0]["credential"]["token"]
        self.assertFalse(custody.read_custodied_credential_status(token)["usable"])
        self.send.assert_awaited_once()

    async def test_native_http_requires_host_user_and_never_echoes_the_bootstrap_nonce(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppCredentialCustodyApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env
        app = FastAPI(); app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        challenge = self.challenge()
        body = {"expected_revision": 1, "request_id": "http-context",
                "notification_challenge_uid": challenge["challenge_uid"],
                "notification_challenge_token": challenge["challenge_token"]}
        path = f"/settings/apps/credential-custody/{self.grant['custody_uid']}/handoff"
        with TestClient(app) as client:
            self.assertEqual(client.post(path, json=body).status_code, 401)
            self.assertEqual(client.post(path, json=body,
                headers={"Authorization": "Bearer " + self.token["token"]}).status_code, 401)
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            metadata = client.get(f"/settings/apps/connections/{self.connection.get_uid()}/credential-custody", headers=headers)
            self.assertEqual(metadata.status_code, 200, metadata.text)
            self.assertEqual(metadata.json()["connection_revision"], self.connection_revision())
            self.assertTrue(metadata.json()["approved_by_me"])
            self.assertTrue(metadata.json()["current"])
            self.assertNotIn(challenge["challenge_token"], metadata.text)
            partial = client.post(path, json={**body, "notification_challenge_uid": None}, headers=headers)
            self.assertEqual(partial.status_code, 400, partial.text)
            self.assertNotIn(challenge["challenge_token"], partial.text)
            delivered = client.post(path, json=body, headers=headers)
            self.assertEqual(delivered.status_code, 200, delivered.text)
            self.assertEqual(delivered.headers["cache-control"], "no-store")
            self.assertEqual(delivered.json()["state"], "delivered")
            self.assertNotIn(challenge["challenge_token"], delivered.text)
            self.assertNotIn(self.received[0]["credential"]["token"], delivered.text)
            changed = client.post(path, json={**body, "notification_challenge_token": "X" * 43}, headers=headers)
            self.assertEqual(changed.status_code, 403, changed.text)
            self.assertNotIn("X" * 43, changed.text)
            self.send.assert_awaited_once()

    async def test_generated_bootstrap_migration_preserves_legacy_and_retained_challenge_history(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import create_engine, text, inspect
        from sqlalchemy.exc import IntegrityError
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261117003610-d8a1a63bf1fc.py"
        spec = importlib.util.spec_from_file_location("notification_bootstrap_schema", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE app_connection_credential_handoff (id BIGINT PRIMARY KEY, request_id TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO app_connection_credential_handoff VALUES (1,'legacy-one'),(2,'legacy-two')"))
            module.op = Operations(MigrationContext.configure(connection)); module.upgrade()
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_connection_credential_handoff WHERE notification_challenge_id IS NULL")).scalar_one(), 2)
            connection.execute(text("UPDATE app_connection_credential_handoff SET notification_challenge_id=7 WHERE id=1"))
            with self.assertRaises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("UPDATE app_connection_credential_handoff SET notification_challenge_id=7 WHERE id=2"))
            with self.assertRaisesRegex(RuntimeError, "history"): module.downgrade()
            self.assertEqual(connection.execute(text("SELECT notification_challenge_id FROM app_connection_credential_handoff WHERE id=1")).scalar_one(), 7)
            connection.execute(text("UPDATE app_connection_credential_handoff SET notification_challenge_id=NULL")); module.downgrade()
            self.assertNotIn("notification_challenge_id", {row["name"] for row in inspect(connection).get_columns("app_connection_credential_handoff")})
            self.assertEqual(connection.execute(text("SELECT request_id FROM app_connection_credential_handoff ORDER BY id")).scalars().all(), ['legacy-one', 'legacy-two'])
