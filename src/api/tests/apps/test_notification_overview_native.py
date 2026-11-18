"""Owner-only bounded Outbox reads, source redaction and distinct delivery evidence."""
import json
import unittest
from unittest.mock import patch
from test_notification_delivery_native import NotificationDeliveryNativeTest
from test_notification_activity_native import NotificationActivityNativeTest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import AppExecutionOutbox
from langboard_shared.domain.services.AppNotificationOverview import (
    list_personal_notification_outbox, read_personal_notification_outbox, read_connection_notification_settings,
)
from langboard_shared.domain.services.AppNotificationDelivery import create_personal_subscription
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, finish_app_event


class NotificationOverviewNativeTest(unittest.TestCase):
    connection_revision = NotificationDeliveryNativeTest.connection_revision
    challenge = NotificationDeliveryNativeTest.challenge
    proof = NotificationDeliveryNativeTest.proof
    approve = NotificationDeliveryNativeTest.approve
    verified = NotificationDeliveryNativeTest.verified
    admission = NotificationDeliveryNativeTest.admission
    event = NotificationDeliveryNativeTest.event
    receipt = NotificationDeliveryNativeTest.receipt

    def setUp(self):
        NotificationDeliveryNativeTest.setUp(self)

    def test_owner_settings_are_bounded_and_do_not_return_challenge_secrets(self):
        for index in range(3):
            create_personal_subscription(self.owner, self.destination_id,
                expected_destination_revision=1, request_id=f"extra-{index}")
        page = read_connection_notification_settings(self.owner, self.connection.id, section="subscriptions", limit=2)
        self.assertEqual(len(page["items"]), 2)
        self.assertFalse(page["delivery_authority"])
        following = read_connection_notification_settings(self.owner, self.connection.id, section="subscriptions",
            limit=2, after=SnowflakeID.from_short_code(page["next_cursor"]))
        self.assertEqual(len(following["items"]), 2)
        self.assertIsNone(following["next_cursor"])
        self.assertFalse({x["subscription_uid"] for x in page["items"]} & {x["subscription_uid"] for x in following["items"]})
        for section in ("destinations", "challenges", "subscriptions"):
            result = read_connection_notification_settings(self.owner, self.connection.id, section=section)
            encoded = json.dumps(result)
            self.assertNotIn('"challenge_token"', encoded)
            self.assertNotIn('"token_hash"', encoded)
            self.assertNotIn(self.token["token"], encoded)
            with self.assertRaises(AppGovernanceDenied):
                read_connection_notification_settings(self.other, self.connection.id, section=section)
        with self.assertRaises(ValueError):read_connection_notification_settings(self.owner, self.connection.id, limit=51)

    def test_outbox_list_is_neutral_owner_metadata_and_detail_is_separately_checked(self):
        self.event()
        page = list_personal_notification_outbox(self.owner)
        self.assertEqual(len(page["items"]), 1)
        item = page["items"][0]
        self.assertEqual(item["status"], "pending")
        self.assertNotIn("notification", item)
        self.assertNotIn("opaque-approved-binding", json.dumps(page))
        self.assertFalse(item["delivery_authority"])
        self.assertEqual(list_personal_notification_outbox(self.other)["items"], [])
        identifier = SnowflakeID.from_short_code(item["dispatch_uid"])
        with self.assertRaises(AppGovernanceDenied):read_personal_notification_outbox(self.other, identifier)
        detail = read_personal_notification_outbox(self.owner, identifier)
        self.assertTrue(detail["content_available"])
        self.assertEqual(detail["notification"]["body"], "Synthetic user text")
        self.assertFalse(detail["delivery_authority"])
        self.queue.assert_called_once()

    def test_transport_ack_unknown_and_provider_confirmation_are_distinct(self):
        event_id = self.event()
        signed = claim_app_event(event_id, 1)
        finish_app_event(event_id, signed["claim_token"], delivered=True)
        self.assertEqual(list_personal_notification_outbox(self.owner)["items"][0]["status"], "transport_delivered")
        self.receipt(event_id, "adapter", "adapter_accepted")
        self.assertEqual(list_personal_notification_outbox(self.owner)["items"][0]["status"], "adapter_accepted")
        self.receipt(event_id, "uncertain", "unknown", expected_revision=1)
        self.assertEqual(list_personal_notification_outbox(self.owner)["items"][0]["status"], "unknown")
        self.receipt(event_id, "provider", "confirmed", expected_revision=2, reconciled=True, provider_message_id="test-provider-1")
        self.assertEqual(list_personal_notification_outbox(self.owner)["items"][0]["status"], "confirmed")
        self.queue.assert_called_once()

    def test_list_query_count_does_not_grow_with_receipt_count_and_pages_do_not_overlap(self):
        from sqlalchemy import event
        self.event()
        counts = []
        def record(*args):counts.append(1)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            list_personal_notification_outbox(self.owner)
            one = len(counts)
        finally:event.remove(self.engine, "before_cursor_execute", record)
        for index in range(9):self.admission(f"message-{index}")
        counts.clear();event.listen(self.engine, "before_cursor_execute", record)
        try:
            page = list_personal_notification_outbox(self.owner, limit=5)
            many = len(counts)
        finally:event.remove(self.engine, "before_cursor_execute", record)
        self.assertLessEqual(many, one + 1)
        following = list_personal_notification_outbox(self.owner, limit=5,
            after=SnowflakeID.from_short_code(page["next_cursor"]))
        self.assertEqual(len(page["items"]), 5);self.assertEqual(len(following["items"]), 5)
        self.assertFalse({x["dispatch_uid"] for x in page["items"]} & {x["dispatch_uid"] for x in following["items"]})
        self.assertIsNone(following["next_cursor"])

    def test_native_http_requires_current_user_and_bounds_cursors(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppNotificationDeliveryApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity, KeyVault
        from langboard_shared.Env import Env
        self.event()
        app = FastAPI();app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        with patch.object(KeyVault, "get_key", return_value="notification-test-signing-key-" * 4), TestClient(app) as client:
            path = "/settings/apps/notification-outbox"
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers={"Authorization": "Bearer " + self.token["token"]}).status_code, 401)
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            response = client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(len(response.json()["items"]), 1)
            self.assertIn(client.get(path + "?limit=51", headers=headers).status_code, (400, 422))
            self.assertIn(client.get(path + "?after=not-a-cursor", headers=headers).status_code, (400, 422))
            connection = f"/settings/apps/connections/{self.connection.get_uid()}/notification-settings"
            self.assertEqual(client.get(connection, headers=headers).status_code, 200)
            self.assertIn(client.get(connection + "?section=credentials", headers=headers).status_code, (400, 422))
            other_access, other_refresh = AuthSecurity.authenticate(self.other.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, other_refresh)
            other_headers = {"Authorization": "Bearer " + other_access}
            self.assertEqual(client.get(connection, headers=other_headers).status_code, 403)
            self.assertEqual(client.get(path, headers=other_headers).json()["items"], [])
            identifier = response.json()["items"][0]["dispatch_uid"]
            self.assertEqual(client.get(f"{path}/{identifier}", headers=other_headers).status_code, 403)


class NotificationOverviewSourceNativeTest(unittest.TestCase):
    connection_revision = NotificationActivityNativeTest.connection_revision
    challenge = NotificationActivityNativeTest.challenge
    proof = NotificationActivityNativeTest.proof
    approve = NotificationActivityNativeTest.approve
    verified = NotificationActivityNativeTest.verified
    source = NotificationActivityNativeTest.source

    def setUp(self):NotificationActivityNativeTest.setUp(self)

    def test_acl_loss_redacts_detail_but_retains_neutral_owner_receipt(self):
        from langboard_shared.domain.services.AppNotificationActivity import admit_native_notification
        row, _ = self.source();admit_native_notification(row.id)
        item = list_personal_notification_outbox(self.owner)["items"][0]
        identifier = SnowflakeID.from_short_code(item["dispatch_uid"])
        self.assertTrue(read_personal_notification_outbox(self.owner, identifier)["content_available"])
        with DbSession.atomic() as db:
            self.card.visibility = "PRIVATE";self.card.owner_user_id = self.owner.id;db.update(self.card)
        detail = read_personal_notification_outbox(self.owner, identifier)
        self.assertFalse(detail["content_available"]);self.assertIsNone(detail["notification"])
        self.assertNotIn(self.card.title, json.dumps(detail))
        self.assertEqual(list_personal_notification_outbox(self.owner)["items"][0]["dispatch_uid"], item["dispatch_uid"])
