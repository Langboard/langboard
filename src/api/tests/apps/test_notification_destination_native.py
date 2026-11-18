"""Native persistence and current-authority consent tests using an isolated DB."""

import tempfile
import importlib.util
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import PropertyMock, patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from langboard_shared.Env import Env
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppConnection, AppConnectionCredential, AppDefinition, AppGovernancePolicy, Organization, User,
)
from langboard_shared.domain.models.AppNotificationDestination import (
    AppNotificationDestination, AppNotificationDestinationChallenge,
)
from langboard_shared.domain.models import AppNotificationDestinationSchema  # noqa: F401
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential, revoke_connection_credential
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.AppNotificationDestinations import (
    approve_destination_challenge, cancel_destination_challenge, create_destination_challenge,
    current_notification_destination, read_destination_challenge, read_notification_destination,
    revoke_notification_destination, submit_destination_proof,
)


DECLARATION = {
    "schema_version": 1, "key": "notification-test", "version": "1.0.0",
    "name": "Notification test", "description": "Explicit personal consent test",
    "capabilities": ["events.receive"], "resource_types": [],
    "trust": {"authentication_origins": ["https://identity.example"], "data_origins": [], "mcp_origins": []},
}
IDENTITY = {"issuer": "https://identity.example/tenant/v2", "subject": "verified-subject"}
DESTINATION = {"kind": "personal", "external_id": "opaque-approved-binding", "display_name": "Verified account"}


class NotificationDestinationNativeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="notification-consent-test-")
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        for attribute in ("get_main_engine", "get_readonly_engine"):
            mocked = patch.object(DbEngine, attribute, return_value=self.engine)
            mocked.start()
            self.addCleanup(mocked.stop)
        cache = patch.object(type(Env), "CACHE_DIR", new_callable=PropertyMock, return_value=Path(self.directory.name) / "cache")
        cache.start()
        self.addCleanup(cache.stop)
        for model in (User, Organization, AppDefinition, AppGovernancePolicy, AppConnection, AppConnectionCredential,
                      AppNotificationDestinationChallenge, AppNotificationDestination):
            model.__table__.create(self.engine)
        self.owner = User(firstname="Consent", lastname="Owner", email="owner@example.invalid", password="test", activated_at=SafeDateTime.now())
        self.other = User(firstname="Other", lastname="Admin", email="other@example.invalid", password="test", activated_at=SafeDateTime.now(), is_admin=True)
        with DbSession.atomic() as db:
            db.insert(self.owner)
            db.insert(self.other)
        self.definition = AppDefinition(key=DECLARATION["key"], approved_by=self.other.id, declaration=DECLARATION)
        self.connection = AppConnection(app_key=DECLARATION["key"], owner_id=self.owner.id, state="connected")
        with DbSession.atomic() as db:
            for row in (self.definition, self.connection):
                db.insert(row)
        self.token = issue_connection_credential(self.owner, self.connection.id)

    def connection_revision(self):
        from langboard_shared.domain.services.AppConnectionManagement import list_inbound_connections
        items = list_inbound_connections(self.owner, self.connection.app_key)["items"]
        return next(row["revision"] for row in items if row["connection_uid"] == self.connection.get_uid())

    def challenge(self, request_id="challenge-1"):
        return create_destination_challenge(self.owner, self.connection.id, request_id=request_id,
                                            expected_connection_revision=self.connection_revision())

    def proof(self, challenge, *, token=None, identity=None, destination=None, request_id="proof-1"):
        return submit_destination_proof(token or self.token["token"], SnowflakeID.from_short_code(challenge["challenge_uid"]),
                                        challenge_token=challenge["challenge_token"], request_id=request_id,
                                        external_identity=identity or IDENTITY, destination=destination or DESTINATION)

    def approve(self, challenge, proof, *, actor=None):
        return approve_destination_challenge(actor or self.owner, SnowflakeID.from_short_code(challenge["challenge_uid"]),
                                             expected_revision=proof["revision"], proof_id=SnowflakeID.from_short_code(proof["proof_uid"]))

    def verified(self):
        challenge = self.challenge()
        proof = self.proof(challenge)
        return challenge, proof, self.approve(challenge, proof)

    def test_challenge_secret_is_once_only_and_not_stored_or_read(self):
        first = self.challenge()
        repeated = self.challenge()
        self.assertEqual(first["challenge_uid"], repeated["challenge_uid"])
        self.assertNotIn("challenge_token", repeated)
        read = read_destination_challenge(self.owner, SnowflakeID.from_short_code(first["challenge_uid"]))
        self.assertNotIn("token_hash", read)
        self.assertNotIn(first["challenge_token"], str(read))
        with DbSession.atomic() as db:
            rows = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).all()
            self.assertEqual(len(rows), 1)
            self.assertNotEqual(rows[0].token_hash, first["challenge_token"])
            self.assertNotIn(first["challenge_token"], repr(rows[0]))

    def test_app_proof_does_not_activate_before_user_approval(self):
        challenge = self.challenge()
        proof = self.proof(challenge)
        self.assertEqual(proof["state"], "awaiting_user_approval")
        with DbSession.atomic() as db:
            self.assertEqual(db.exec(SqlBuilder.select.table(AppNotificationDestination)).all(), [])
        destination = self.approve(challenge, proof)
        self.assertEqual((destination["kind"], destination["state"], destination["revision"]), ("personal", "verified", 1))
        with DbSession.atomic() as db:
            row = current_notification_destination(db, SnowflakeID.from_short_code(destination["destination_uid"]), expected_revision=1)
            self.assertEqual(row.user_id, self.owner.id)
        status = read_notification_destination(self.owner, SnowflakeID.from_short_code(destination["destination_uid"]))
        self.assertNotIn("proof", status)
        self.assertNotIn(DESTINATION["external_id"], str(status))

    def test_other_user_even_admin_cannot_read_approve_or_revoke_personal_target(self):
        challenge, proof, destination = self.verified()
        with self.assertRaises(AppGovernanceDenied):
            read_destination_challenge(self.other, SnowflakeID.from_short_code(challenge["challenge_uid"]))
        with self.assertRaises(AppGovernanceDenied):
            self.approve(challenge, proof, actor=self.other)
        with self.assertRaises(AppGovernanceDenied):
            revoke_notification_destination(self.other, SnowflakeID.from_short_code(destination["destination_uid"]), expected_revision=1)
        with self.assertRaises(AppGovernanceDenied):
            self.challenge_for_other()

    def challenge_for_other(self):
        return create_destination_challenge(self.other, self.connection.id, request_id="other",
                                            expected_connection_revision=self.connection_revision())

    def test_wrong_connection_or_secret_cannot_attest(self):
        challenge = self.challenge()
        with DbSession.atomic() as db:
            other = AppConnection(app_key=DECLARATION["key"], owner_id=self.other.id, state="connected")
            db.insert(other)
        token = issue_connection_credential(self.other, other.id)["token"]
        with self.assertRaises(AppGovernanceDenied):
            self.proof(challenge, token=token)
        altered = {**challenge, "challenge_token": "x" * 43}
        with self.assertRaises(AppGovernanceDenied):
            self.proof(altered)
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).first()
            self.assertEqual(row.state, "pending")

    def test_proof_replay_is_idempotent_but_changed_routing_is_conflict(self):
        challenge = self.challenge()
        first = self.proof(challenge)
        self.assertEqual(self.proof(challenge), first)
        with self.assertRaises(AppGovernanceConflict):
            self.proof(challenge, destination={**DESTINATION, "external_id": "other-destination"})
        with self.assertRaises(AppGovernanceConflict):
            self.proof(challenge, request_id="another-proof")
        self.approve(challenge, first)
        with self.assertRaises(AppGovernanceConflict):
            self.approve(challenge, first)

    def test_unapproved_issuer_and_nonpersonal_proof_never_persist(self):
        challenge = self.challenge()
        with self.assertRaises(AppGovernanceDenied):
            self.proof(challenge, identity={**IDENTITY, "issuer": "https://unapproved.example/tenant"})
        for issuer in ("http://identity.example", "https://identity.example/?token=secret", "https://user@identity.example"):
            with self.subTest(issuer=issuer), self.assertRaises(ValueError):
                self.proof(challenge, identity={**IDENTITY, "issuer": issuer})
        with self.assertRaises(ValueError):
            self.proof(challenge, destination={**DESTINATION, "kind": "shared"})
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).first()
            self.assertEqual(row.proof, {})

    def test_expiry_blocks_proof_and_approval(self):
        first = self.challenge()
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).first()
            row.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(row)
        with self.assertRaises(AppGovernanceConflict):
            self.proof(first)
        second = self.challenge("challenge-2")
        proof = self.proof(second)
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
                AppNotificationDestinationChallenge.id == SnowflakeID.from_short_code(second["challenge_uid"]))).first()
            row.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(row)
        with self.assertRaises(AppGovernanceConflict):
            self.approve(second, proof)

    def test_revoked_attestation_credential_blocks_pending_approval(self):
        challenge = self.challenge()
        proof = self.proof(challenge)
        revoke_connection_credential(self.owner, self.connection.id, SnowflakeID.from_short_code(self.token["credential_uid"]))
        with self.assertRaises(AppGovernanceDenied):
            self.approve(challenge, proof)

    def test_destination_revoke_retains_history_and_requires_new_approval(self):
        challenge, _, destination = self.verified()
        identifier = SnowflakeID.from_short_code(destination["destination_uid"])
        revoked = revoke_notification_destination(self.owner, identifier, expected_revision=1)
        self.assertEqual((revoked["state"], revoked["revision"]), ("revoked", 2))
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceDenied):
            current_notification_destination(db, identifier, expected_revision=1)
        with self.assertRaises(AppGovernanceConflict):
            revoke_notification_destination(self.owner, identifier, expected_revision=1)
        second = self.challenge("challenge-2")
        second_proof = self.proof(second)
        reapproved = self.approve(second, second_proof)
        self.assertEqual((reapproved["destination_uid"], reapproved["revision"]), (destination["destination_uid"], 3))
        old = read_destination_challenge(self.owner, SnowflakeID.from_short_code(challenge["challenge_uid"]))
        self.assertEqual(old["state"], "verified")
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceConflict):
            current_notification_destination(db, identifier, expected_revision=1)

    def test_cancelled_challenge_never_creates_destination(self):
        challenge = self.challenge()
        identifier = SnowflakeID.from_short_code(challenge["challenge_uid"])
        cancel_destination_challenge(self.owner, identifier, expected_revision=1)
        with self.assertRaises(AppGovernanceConflict):
            self.proof(challenge)

    def test_current_policy_is_rechecked_and_cleanup_remains_available(self):
        _, _, destination = self.verified()
        identifier = SnowflakeID.from_short_code(destination["destination_uid"])
        with DbSession.atomic() as db:
            db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceDenied):
            current_notification_destination(db, identifier)
        # Owner can still inspect and revoke after the shared app policy closes.
        self.assertEqual(read_notification_destination(self.owner, identifier)["state"], "verified")
        self.assertEqual(revoke_notification_destination(self.owner, identifier, expected_revision=1)["state"], "revoked")

    def test_changed_connection_identity_blocks_current_destination(self):
        _, _, destination = self.verified()
        with DbSession.atomic() as db:
            self.connection.updated_at += timedelta(seconds=1)
            db.update(self.connection)
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceConflict):
            current_notification_destination(db, SnowflakeID.from_short_code(destination["destination_uid"]))

    def test_deactivated_owner_blocks_current_destination(self):
        _, _, destination = self.verified()
        with DbSession.atomic() as db:
            self.owner.activated_at = None
            db.update(self.owner)
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceDenied):
            current_notification_destination(db, SnowflakeID.from_short_code(destination["destination_uid"]))

    def test_approval_from_different_connection_cannot_support_destination(self):
        challenge, _, destination = self.verified()
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
                AppNotificationDestinationChallenge.id == SnowflakeID.from_short_code(challenge["challenge_uid"]))).first()
            row.connection_id = SnowflakeID()
            db.update(row)
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceDenied):
            current_notification_destination(db, SnowflakeID.from_short_code(destination["destination_uid"]))

    def test_database_rejects_incomplete_proof_and_invalid_state(self):
        challenge = self.challenge()
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as db:
                db.execute(AppNotificationDestinationChallenge.__table__.update().values(state="verified"))
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as db:
                db.execute(AppNotificationDestinationChallenge.__table__.update().values(revision=0))
        self.assertEqual(read_destination_challenge(self.owner, SnowflakeID.from_short_code(challenge["challenge_uid"]))["state"], "pending")

    def test_generated_migration_upgrade_schema_and_retained_history(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect, text
        path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261116202430-c4f143949d80.py"
        spec = importlib.util.spec_from_file_location("notification_consent_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            self.assertEqual(set(inspect(connection).get_table_names()), {
                "app_notification_destination", "app_notification_destination_challenge"})
            migration.downgrade()
            self.assertEqual(inspect(connection).get_table_names(), [])
            migration.upgrade()
            connection.execute(text("INSERT INTO app_notification_destination_challenge "
                "(id,user_id,connection_id,request_id,app_key,connection_identity,token_hash,state,revision,proof) "
                "VALUES (1,2,3,'retained','test','identity','hash','pending',1,'{}')"))
            with self.assertRaisesRegex(RuntimeError, "retained"):
                migration.downgrade()
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_notification_destination_challenge")).scalar_one(), 1)

    def test_native_http_user_consent_and_app_proof_are_separate(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppNotificationDestinationApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity, KeyVault
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        # Real token cryptography and the native middleware run with a test-only
        # signing key. No operational credential is minted, fetched or printed.
        with patch.object(KeyVault, "get_key", return_value="notification-test-signing-key-" * 4), TestClient(app) as client:
            path = f"/settings/apps/connections/{self.connection.get_uid()}/notification-destination-challenges"
            body = {"request_id": "http-1", "expected_connection_revision": self.connection_revision(), "kind": "personal"}
            self.assertEqual(client.post(path, json=body).status_code, 401)
            self.assertEqual(client.post(path, json=body, headers={"Authorization": "Bearer " + self.token["token"]}).status_code, 401)
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            user_headers = {"Authorization": "Bearer " + access}
            response = client.post(path, json=body, headers=user_headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            challenge = response.json()
            proof_body = {"challenge_uid": challenge["challenge_uid"], "challenge_token": challenge["challenge_token"],
                          "request_id": "http-proof-1", "external_identity": IDENTITY, "destination": DESTINATION}
            self.assertEqual(client.post("/apps/v1/notification-destination-proofs", json=proof_body).status_code, 401)
            # The user's valid native bearer is not an app connection credential.
            self.assertEqual(client.post("/apps/v1/notification-destination-proofs", json=proof_body, headers=user_headers).status_code, 403)
            proof_response = client.post("/apps/v1/notification-destination-proofs", json=proof_body,
                                         headers={"Authorization": "Bearer " + self.token["token"]})
            self.assertEqual(proof_response.status_code, 200, proof_response.text)
            proof = proof_response.json()
            approved = client.post(f"/settings/apps/notification-destination-challenges/{challenge['challenge_uid']}/approve",
                                   json={"proof_uid": proof["proof_uid"], "expected_revision": proof["revision"]}, headers=user_headers)
            self.assertEqual(approved.status_code, 200, approved.text)
            destination = approved.json()
            status = client.get(f"/settings/apps/notification-destinations/{destination['destination_uid']}", headers=user_headers)
            self.assertEqual(status.status_code, 200)
            self.assertNotIn(DESTINATION["external_id"], status.text)
            revoked = client.post(f"/settings/apps/notification-destinations/{destination['destination_uid']}/revoke",
                                  json={"expected_revision": 1}, headers=user_headers)
            self.assertEqual(revoked.status_code, 200, revoked.text)
            self.assertEqual(revoked.json()["state"], "revoked")

    def test_native_http_forbids_extra_identity_fields_and_unknown_scope(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppNotificationDestinationApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity, KeyVault
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        with patch.object(KeyVault, "get_key", return_value="notification-test-signing-key-" * 4), TestClient(app) as client:
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            path = f"/settings/apps/connections/{self.connection.get_uid()}/notification-destination-challenges"
            body = {"request_id": "http-invalid", "expected_connection_revision": self.connection_revision(), "kind": "personal"}
            for values in ({**body, "kind": "unknown"}, {**body, "user_uid": self.other.get_uid()}):
                with self.subTest(values=values):
                    self.assertIn(client.post(path, json=values, headers=headers).status_code, (400, 422))
            with DbSession.atomic() as db:
                self.assertEqual(db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge)).all(), [])


if __name__ == "__main__":
    unittest.main()
