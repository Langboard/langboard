"""Secret-free durable custody and fixed callback lifecycle."""

import importlib
import json
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
        renewed = await custody.renew_custodied_credential(original, request_id="renew-1")
        self.assertEqual(renewed["state"], "delivered")
        self.assertEqual(len(self.received), 2)
        with self.assertRaises(AppGovernanceDenied):
            authenticate_connection_credential(original)
        self.assertEqual(authenticate_connection_credential(self.received[1]["credential"]["token"]).connection_id, self.connection.id)
