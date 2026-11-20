"""Persisted notification admission and consumer fences with the existing outbox."""

import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from test_notification_destination_native import NotificationDestinationNativeTest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security import KeyVault
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import AppConnection, AppExecutionOutbox, AppExecutionRequest, AppEventDestination, WebhookSetting
from langboard_shared.domain.models.AppNotificationSubscription import AppNotificationSubscription, AppNotificationDispatch
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, AppGovernanceConflict
from langboard_shared.domain.services.AppEventDestination import APP_NOTIFICATION_EVENT, bind_app_event_destination
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, finish_app_event
from langboard_shared.domain.services.AppNotificationDelivery import (
    create_personal_subscription, configure_personal_subscription, revoke_personal_subscription, create_test_notification,
    authorize_notification_delivery, record_notification_receipt,
)
from langboard_shared.domain.services.AppNotificationDestinations import revoke_notification_destination
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential, revoke_connection_credential


class NotificationDeliveryNativeTest(unittest.TestCase):
    connection_revision = NotificationDestinationNativeTest.connection_revision
    challenge = NotificationDestinationNativeTest.challenge
    proof = NotificationDestinationNativeTest.proof
    approve = NotificationDestinationNativeTest.approve
    verified = NotificationDestinationNativeTest.verified

    def setUp(self):
        NotificationDestinationNativeTest.setUp(self)
        for model in (AppExecutionOutbox, AppExecutionRequest, AppEventDestination, WebhookSetting,
                      AppNotificationSubscription, AppNotificationDispatch):
            model.__table__.create(self.engine)
        with DbSession.atomic() as db:
            declaration = self.definition.declaration
            self.definition.declaration = {**declaration, "trust": {
                **declaration["trust"], "data_origins": ["https://approved.example"]}}
            db.update(self.definition)
            setting = WebhookSetting(name="Notifications", url="https://approved.example/events",
                secret_id="test-notification-signing", events=[APP_NOTIFICATION_EVENT])
            db.insert(setting)
        bind_app_event_destination(self.other, self.connection.id, setting.id, None)
        self.token = issue_connection_credential(self.owner, self.connection.id)
        _, _, self.destination = self.verified()
        self.destination_id = SnowflakeID.from_short_code(self.destination["destination_uid"])
        self.subscription = create_personal_subscription(self.owner, self.destination_id,
            expected_destination_revision=1, request_id="subscribe")
        self.subscription_id = SnowflakeID.from_short_code(self.subscription["subscription_uid"])
        self.queue = patch("langboard_shared.domain.services.AppEventDelivery.enqueue_event").start()
        self.addCleanup(patch.stopall)
        self.signing = patch.object(KeyVault, "get_key", return_value="test-signing").start()

    def admission(self, request_id="message"):
        return create_test_notification(self.owner, self.subscription_id, expected_generation=1,
                                        request_id=request_id, title="Test", body="Synthetic user text")

    def event(self):
        result = self.admission()
        return SnowflakeID.from_short_code(result["event_uid"])

    def authorize(self, event_id, **overrides):
        return authorize_notification_delivery(self.token["token"], event_id,
            **{"event_revision": 1, "subscription_generation": 1, "destination_revision": 1, **overrides})

    def receipt(self, event_id, request_id, status, **kwargs):
        return record_notification_receipt(self.token["token"], event_id, request_id=request_id,
            event_revision=1, subscription_generation=1, destination_revision=1, status=status, **kwargs)

    def test_test_subscription_never_expands_and_scope_changes_fence_existing_events(self):
        self.assertEqual(self.subscription["event_kinds"], ["test"])
        self.assertEqual(self.subscription["project_uids"], [])
        event_id = self.event()
        with self.assertRaises(AppGovernanceDenied):
            configure_personal_subscription(self.other, self.subscription_id, expected_generation=1,
                event_kinds=["card_activity"], project_uids=[])
        changed = configure_personal_subscription(self.owner, self.subscription_id, expected_generation=1,
            event_kinds=["card_activity"], project_uids=[])
        self.assertEqual((changed["generation"], changed["event_kinds"]), (2, ["card_activity"]))
        with self.assertRaises(AppGovernanceConflict):
            self.authorize(event_id)
        with self.assertRaises(AppGovernanceDenied):
            create_test_notification(self.owner, self.subscription_id, expected_generation=2,
                request_id="test-off", title="Test", body="Synthetic")
        with self.assertRaises(AppGovernanceConflict):
            configure_personal_subscription(self.owner, self.subscription_id, expected_generation=1,
                event_kinds=["test"], project_uids=[])
        same = configure_personal_subscription(self.owner, self.subscription_id, expected_generation=2,
            event_kinds=["card_activity"], project_uids=[])
        self.assertEqual(same["generation"], 2)

    def test_deadline_scope_requires_selected_currently_readable_boards(self):
        for kinds, projects in [([], []), (["card_deadline"], []), (["invented"], []),
                                (["test", "test"], []), (["card_activity"], ["invalid"]),
                                (["card_activity"], ["x" * 11, "x" * 11])]:
            with self.assertRaises(ValueError):
                configure_personal_subscription(self.owner, self.subscription_id, expected_generation=1,
                    event_kinds=kinds, project_uids=projects)
        from langboard_shared.domain.services.factory.CardService import CardService
        with patch.object(CardService, "resolve_work_visibility_contexts", return_value={}):
            with self.assertRaises(AppGovernanceDenied):
                configure_personal_subscription(self.owner, self.subscription_id, expected_generation=1,
                    event_kinds=["card_deadline"], project_uids=[SnowflakeID().to_short_code()])
        self.assertEqual(self.queue.call_count, 0)

    def test_generated_scope_migration_backfills_test_only_and_preserves_explicit_consent(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import create_engine, inspect, text
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        source = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261118053109-e53ce176b15e.py"
        spec = importlib.util.spec_from_file_location("notification_scope_migration", source)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE app_notification_subscription (id BIGINT PRIMARY KEY, state TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO app_notification_subscription VALUES (1,'enabled')"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            self.assertEqual(connection.execute(text("SELECT filters FROM app_notification_subscription")).scalar_one(), "{}")
            self.assertEqual(connection.execute(text("SELECT state FROM app_notification_subscription")).scalar_one(), "enabled")
            migration.downgrade()
            self.assertNotIn("filters", {row["name"] for row in inspect(connection).get_columns("app_notification_subscription")})
            migration.upgrade()
            connection.execute(text("UPDATE app_notification_subscription SET filters=:scope"),
                               {"scope": json.dumps({"event_kinds": ["card_activity"], "project_uids": []})})
            with self.assertRaisesRegex(RuntimeError, "Retain explicit notification scopes"):
                migration.downgrade()
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_notification_subscription")).scalar_one(), 1)

    def test_explicit_subscription_is_owner_only_idempotent_and_revision_fenced(self):
        self.assertEqual(self.subscription["scope"], "personal")
        self.assertEqual(create_personal_subscription(self.owner, self.destination_id,
            expected_destination_revision=1, request_id="subscribe"), self.subscription)
        with self.assertRaises(AppGovernanceDenied):
            create_personal_subscription(self.other, self.destination_id, expected_destination_revision=1, request_id="other")
        with self.assertRaises(AppGovernanceConflict):
            create_personal_subscription(self.owner, self.destination_id, expected_destination_revision=2, request_id="stale")
        with self.assertRaises(AppGovernanceDenied):
            revoke_personal_subscription(self.other, self.subscription_id, expected_generation=1)

    def test_one_outbox_event_is_signed_without_claiming_provider_delivery(self):
        first = self.admission(); second = self.admission()
        self.assertEqual(first["event_uid"], second["event_uid"])
        self.assertFalse(second["created"])
        self.queue.assert_called_once()
        event_id = SnowflakeID.from_short_code(first["event_uid"])
        with self.assertRaises(AppGovernanceConflict):
            create_test_notification(self.owner, self.subscription_id, expected_generation=1,
                request_id="message", title="Changed", body="Synthetic user text")
        signed = claim_app_event(event_id, 1)
        self.assertIsNotNone(signed)
        from langboard_sdk.webhooks import verify_webhook
        verify_webhook(signed["body"], signed["headers"], "test-signing")
        payload = json.loads(signed["body"])
        self.assertEqual(payload["external_destination"], {"kind": "personal", "external_id": "opaque-approved-binding"})
        self.assertEqual(payload["notification_fence"]["subscription_generation"], 1)
        finish_app_event(event_id, signed["claim_token"], delivered=True)
        self.assertTrue(self.authorize(event_id)["authorized"])
        with DbSession.atomic() as db:
            dispatch = db.exec(SqlBuilder.select.table(AppNotificationDispatch)).first()
            self.assertEqual(dispatch.receipt, {})
            self.assertEqual(len(db.exec(SqlBuilder.select.table(AppExecutionOutbox)).all()), 1)

    def test_revocation_blocks_both_host_delivery_and_later_consumer_send(self):
        event_id = self.event()
        self.assertTrue(self.authorize(event_id)["authorized"])
        revoked = revoke_personal_subscription(self.owner, self.subscription_id, expected_generation=1)
        self.assertEqual((revoked["state"], revoked["generation"]), ("revoked", 2))
        with self.assertRaises(AppGovernanceDenied): self.authorize(event_id)
        self.assertIsNone(claim_app_event(event_id, 1))
        with self.assertRaises(AppGovernanceDenied): self.receipt(event_id, "after-revoke", "adapter_accepted")

    def test_destination_revocation_changed_snapshot_expiry_and_other_connection_are_rejected(self):
        event_id = self.event()
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id, event_revision=2)
        with DbSession.atomic() as db:
            event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
            original = event.payload
            event.payload = {**original, "external_destination": {"kind": "personal", "external_id": "injected"}}
            db.update(event)
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        with DbSession.atomic() as db:
            event.payload = original; db.update(event)
            other = AppConnection(app_key=self.connection.app_key, owner_id=self.other.id, state="connected"); db.insert(other)
        other_token = issue_connection_credential(self.other, other.id)
        with self.assertRaises(AppGovernanceDenied):
            authorize_notification_delivery(other_token["token"], event_id,
                event_revision=1, subscription_generation=1, destination_revision=1)
        with DbSession.atomic() as db:
            dispatch = db.exec(SqlBuilder.select.table(AppNotificationDispatch)).first()
            original_expiry = dispatch.expires_at
            dispatch.expires_at = SafeDateTime.now() - timedelta(seconds=1); db.update(dispatch)
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        with DbSession.atomic() as db:
            dispatch.expires_at = original_expiry; db.update(dispatch)
        revoke_notification_destination(self.owner, self.destination_id, expected_revision=1)
        with self.assertRaises(AppGovernanceDenied): self.authorize(event_id)
        with self.assertRaises(AppGovernanceDenied): self.receipt(event_id, "revoked", "unknown")

    def test_retained_receipt_cas_replay_and_unknown_reconciliation_never_resend(self):
        event_id = self.event()
        admitted = self.receipt(event_id, "admitted", "adapter_accepted")
        self.assertEqual(admitted["receipt"]["revision"], 1)
        unknown = self.receipt(event_id, "unknown", "unknown", expected_revision=1,
                               reason_code="provider_outcome_unknown")
        self.assertEqual(unknown["receipt"]["revision"], 2)
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        self.assertEqual(self.receipt(event_id, "admitted", "adapter_accepted"), admitted)
        with self.assertRaises(AppGovernanceConflict):
            self.receipt(event_id, "readmit", "adapter_accepted", expected_revision=2)
        with self.assertRaises(AppGovernanceConflict):
            self.receipt(event_id, "confirm", "confirmed", expected_revision=2, provider_message_id="provider-1")
        confirmed = self.receipt(event_id, "reconciled", "confirmed", expected_revision=2,
            provider_message_id="provider-1", reconciled=True)
        self.assertEqual(confirmed["receipt"]["revision"], 3)
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        self.assertEqual(self.receipt(event_id, "reconciled", "confirmed", expected_revision=2,
            provider_message_id="provider-1", reconciled=True), confirmed)
        with self.assertRaises(AppGovernanceConflict):
            self.receipt(event_id, "reconciled", "confirmed", expected_revision=2,
                provider_message_id="different", reconciled=True)
        self.queue.assert_called_once()
        with DbSession.atomic() as db:
            dispatch = db.exec(SqlBuilder.select.table(AppNotificationDispatch)).first()
            self.assertEqual(dispatch.receipt["status"], "confirmed")
            self.assertEqual(len(dispatch.receipt_requests), 3)

    def test_native_app_http_uses_exact_fence_and_rejects_injected_fields(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.routes.settings import AppNotificationDeliveryApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        app = FastAPI(); app.include_router(AppRouter.api)
        event_id = self.event(); uid = SnowflakeID(event_id).to_short_code()
        path = "/apps/v1/notification-events/" + uid
        headers = {"authorization": "Bearer " + self.token["token"]}
        fence = {"event_revision": 1, "subscription_generation": 1, "destination_revision": 1}
        with TestClient(app) as client:
            result = client.post(path + "/delivery-authorization", headers=headers, json=fence)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["authorized"])
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertNotIn("opaque-approved-binding", result.text)
            denied = client.post(path + "/delivery-authorization", headers=headers, json={**fence, "url": "https://evil.invalid"})
            self.assertEqual(denied.status_code, 400)
            stale = client.post(path + "/delivery-authorization", headers=headers, json={**fence, "event_revision": 2})
            self.assertEqual(stale.status_code, 409)
            receipt = client.post(path + "/receipts", headers=headers,
                json={**fence, "request_id": "http-admit", "status": "adapter_accepted"})
            self.assertEqual(receipt.status_code, 200, receipt.text)
            self.assertEqual(receipt.json()["receipt"]["status"], "adapter_accepted")

    def test_generated_migration_checks_uniqueness_and_preserves_retained_history(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import create_engine, text, inspect
        from sqlalchemy.exc import IntegrityError
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        source = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261116235504-0967a19d6c63.py"
        spec = importlib.util.spec_from_file_location("notification_delivery_schema", source)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE existing_history (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO existing_history VALUES (1)"))
            module.op = Operations(MigrationContext.configure(connection)); module.upgrade()
            with self.assertRaises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO app_notification_subscription "
                        "(id,user_id,request_id,app_key,connection_id,connection_identity,destination_id,destination_revision,generation,state) "
                        "VALUES (1,2,'request','test',3,'identity',4,1,0,'enabled')"))
            connection.execute(text("INSERT INTO app_notification_subscription "
                "(id,user_id,request_id,app_key,connection_id,connection_identity,destination_id,destination_revision,generation,state) "
                "VALUES (1,2,'request','test',3,'identity',4,1,1,'enabled')"))
            with self.assertRaises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO app_notification_subscription "
                        "(id,user_id,request_id,app_key,connection_id,connection_identity,destination_id,destination_revision,generation,state) "
                        "VALUES (5,2,'request','test',3,'identity',4,1,1,'enabled')"))
            with self.assertRaisesRegex(RuntimeError, "history"): module.downgrade()
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_notification_subscription")).scalar_one(), 1)
            connection.execute(text("DELETE FROM app_notification_subscription"))
            connection.execute(text("INSERT INTO app_notification_dispatch "
                "(id,subscription_id,request_id,snapshot,receipt,receipt_requests) VALUES (6,1,'send','{}','{}','{}')"))
            with self.assertRaisesRegex(RuntimeError, "history"): module.downgrade()
            connection.execute(text("DELETE FROM app_notification_dispatch")); module.downgrade()
            self.assertNotIn("app_notification_dispatch", inspect(connection).get_table_names())
            self.assertEqual(connection.execute(text("SELECT count(*) FROM existing_history")).scalar_one(), 1)


    def test_expired_unknown_reconciliation_retains_cas_replay_and_never_unblocks_send(self):
        event_id = self.event()
        admitted = self.receipt(event_id, "admitted", "adapter_accepted")
        self.receipt(event_id, "unknown", "unknown", expected_revision=1, reason_code="provider_outcome_unknown")
        with patch("langboard_shared.domain.services.AppNotificationDelivery.SafeDateTime", wraps=SafeDateTime) as clock:
            clock.now.return_value = SafeDateTime.now() + timedelta(minutes=11)
            with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
            self.assertIsNone(claim_app_event(event_id, 1))
            self.assertEqual(self.receipt(event_id, "admitted", "adapter_accepted"), admitted)
            for kwargs in ({"expected_revision": 1, "reconciled": True}, {"expected_revision": 2}):
                with self.assertRaises(AppGovernanceConflict):
                    self.receipt(event_id, "stale", "confirmed", provider_message_id="provider-1", **kwargs)
            result = self.receipt(event_id, "late", "confirmed", expected_revision=2,
                provider_message_id="provider-1", reconciled=True)
            self.assertEqual(result["receipt"]["revision"], 3)
            self.assertEqual(self.receipt(event_id, "late", "confirmed", expected_revision=2,
                provider_message_id="provider-1", reconciled=True), result)
            with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        with DbSession.atomic() as db:
            event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
            dispatch = db.exec(SqlBuilder.select.table(AppNotificationDispatch)).first()
            self.assertEqual((event.state, event.attempt_count), ("blocked", 0))
            self.assertEqual(event.payload, dispatch.snapshot)
            self.assertEqual(len(dispatch.receipt_requests), 3)
        self.queue.assert_called_once()

    def test_blocked_receipt_is_observation_only_not_new_admission(self):
        event_id = self.event()
        with DbSession.atomic() as db:
            event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
            event.state = "blocked"; db.update(event)
        with self.assertRaises(AppGovernanceConflict): self.receipt(event_id, "new-admission", "adapter_accepted")
        result = self.receipt(event_id, "observed", "unknown", reason_code="provider_outcome_unknown")
        self.assertEqual(result["receipt"]["revision"], 1)
        with self.assertRaises(AppGovernanceConflict): self.authorize(event_id)
        self.assertIsNone(claim_app_event(event_id, 1))
        self.queue.assert_called_once()

    def test_expired_observation_still_rejects_other_connection_and_tampered_snapshot(self):
        event_id = self.event()
        with DbSession.atomic() as db:
            other = AppConnection(app_key=self.connection.app_key, owner_id=self.other.id, state="connected"); db.insert(other)
        token = issue_connection_credential(self.other, other.id)["token"]
        with patch("langboard_shared.domain.services.AppNotificationDelivery.SafeDateTime", wraps=SafeDateTime) as clock:
            clock.now.return_value = SafeDateTime.now() + timedelta(minutes=11)
            with self.assertRaises(AppGovernanceDenied):
                record_notification_receipt(token, event_id, request_id="cross", event_revision=1,
                    subscription_generation=1, destination_revision=1, status="unknown")
            with DbSession.atomic() as db:
                event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
                event.payload = {**event.payload, "notification": {"body": "changed"}}; db.update(event)
            with self.assertRaises(AppGovernanceConflict): self.receipt(event_id, "changed", "unknown")

    def test_expired_observation_still_rejects_revoked_credential(self):
        event_id = self.event()
        revoke_connection_credential(self.owner, self.connection.id, SnowflakeID.from_short_code(self.token["credential_uid"]))
        with patch("langboard_shared.domain.services.AppNotificationDelivery.SafeDateTime", wraps=SafeDateTime) as clock:
            clock.now.return_value = SafeDateTime.now() + timedelta(minutes=11)
            with self.assertRaises(AppGovernanceDenied): self.receipt(event_id, "revoked", "unknown")

    def test_late_http_receipt_retains_initial_observation_but_never_authorizes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.routes.settings import AppNotificationDeliveryApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        app = FastAPI(); app.include_router(AppRouter.api)
        event_id = self.event(); path = "/apps/v1/notification-events/" + SnowflakeID(event_id).to_short_code()
        headers = {"authorization": "Bearer " + self.token["token"]}
        fence = {"event_revision": 1, "subscription_generation": 1, "destination_revision": 1}
        with patch("langboard_shared.domain.services.AppNotificationDelivery.SafeDateTime", wraps=SafeDateTime) as clock, TestClient(app) as client:
            clock.now.return_value = SafeDateTime.now() + timedelta(minutes=11)
            self.assertEqual(client.post(path + "/delivery-authorization", headers=headers, json=fence).status_code, 409)
            self.assertEqual(client.post(path + "/receipts", headers=headers,
                json={**fence, "request_id": "late-admit", "status": "adapter_accepted"}).status_code, 409)
            body = {**fence, "request_id": "late-observation", "status": "unknown"}
            result = client.post(path + "/receipts", headers=headers, json=body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.post(path + "/receipts", headers=headers, json=body).json(), result.json())
            self.assertEqual(client.post(path + "/receipts", headers=headers,
                json={**body, "receipt_only": True}).status_code, 400)
            self.assertEqual(client.post(path + "/delivery-authorization", headers=headers, json=fence).status_code, 409)
        self.queue.assert_called_once()
