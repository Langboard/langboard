"""Recover expired, unsent native notifications through the existing event queue."""

import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from langboard_sdk.notification_events import parse_notification_event
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import AppExecutionOutbox
from langboard_shared.domain.models.AppNotificationSubscription import AppNotificationDispatch
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, recover_pending_events
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, _current
from langboard_shared.domain.services.AppNotificationActivity import admit_native_notification

from test_notification_activity_native import NotificationActivityNativeTest, WIRE_FIXTURES
from test_shared_notification_native import SharedDeadlineTest


class NotificationRecoveryNativeTest(unittest.TestCase):
    setUp = NotificationActivityNativeTest.setUp
    connection_revision = NotificationActivityNativeTest.connection_revision
    challenge = NotificationActivityNativeTest.challenge
    proof = NotificationActivityNativeTest.proof
    approve = NotificationActivityNativeTest.approve
    verified = NotificationActivityNativeTest.verified
    source = NotificationActivityNativeTest.source
    events = NotificationActivityNativeTest.events
    deadline = NotificationActivityNativeTest.deadline
    authorize = NotificationActivityNativeTest.authorize
    shared_subscription = SharedDeadlineTest.shared_subscription

    def expire(self, event, *, attempts=0, receipt=None):
        expired = SafeDateTime.now() - timedelta(seconds=1)
        with DbSession.atomic() as db:
            current = _current(db, AppExecutionOutbox, event.id)
            dispatch = _current(db, AppNotificationDispatch, current.request_id)
            dispatch.expires_at = expired
            current.attempt_count = attempts
            current.payload = {**current.payload, "expires_at": expired.isoformat()}
            dispatch.snapshot = {**dispatch.snapshot, "payload": current.payload}
            if receipt:
                dispatch.receipt = {"status": receipt}
            db.update(current)
            db.update(dispatch)

    def signed_summary(self, summary, audience, count):
        signed = claim_app_event(summary.id, 1)
        self.assertIsNotNone(signed)
        payload = parse_notification_event(
            signed["body"], signed["headers"], "test-signing",
            expected_app_key=self.connection.app_key, expected_connection_uid=self.connection.get_uid(),
        )
        self.assertEqual(payload["external_destination"]["kind"], audience)
        notification = payload["notification"]
        self.assertEqual(notification["kind"], "missed_summary")
        self.assertEqual(notification["count"], count)
        self.assertEqual(set(notification), {"kind", "title", "body", "count", "window_start", "window_end"})
        self.assertNotIn(self.card.get_uid(), signed["body"].decode())
        self.assertNotIn("missed_event_uids", payload)
        WIRE_FIXTURES[audience + "_missed_summary"] = payload

    def test_only_zero_attempt_receipt_free_sources_are_summarized_once(self):
        for _ in range(6):
            source, _ = self.source()
            self.assertEqual(admit_native_notification(source.id), 1)
        first, second, attempted, unknown, accepted, fresh = self.events()
        self.expire(first)
        self.expire(second)
        self.expire(attempted, attempts=1)
        self.expire(unknown, receipt="unknown")
        self.expire(accepted, receipt="adapter_accepted")

        with patch("langboard_shared.domain.services.AppEventDelivery.enqueue_event") as enqueue:
            recover_pending_events()
            self.assertEqual(len(self.events()), 7)
            summary = self.events()[-1]
            enqueued = {call.args[0] for call in enqueue.call_args_list}
            self.assertNotIn(first.id, enqueued)
            self.assertNotIn(second.id, enqueued)
            self.assertIn(summary.id, enqueued)
            self.assertIn(fresh.id, enqueued)
            recover_pending_events()
            self.assertEqual(len(self.events()), 7)

        self.assertTrue(self.authorize(summary)["authorized"])
        self.signed_summary(summary, "personal", 2)
        self.assertIsNone(claim_app_event(first.id, 1))
        self.assertIsNone(claim_app_event(second.id, 1))
        with DbSession.atomic() as db:
            self.card.visibility = "PRIVATE"
            self.card.owner_user_id = self.owner.id
            db.update(self.card)
        with self.assertRaises(AppGovernanceDenied):
            self.authorize(summary)

    def test_shared_room_summary_uses_native_deadline_only(self):
        _, subscription = self.shared_subscription()
        self.generation = subscription["generation"]
        activity, _ = self.source()
        self.assertEqual(admit_native_notification(activity.id), 0)
        deadline, _ = self.deadline()
        with DbSession.atomic() as db:
            deadline.message_vars = {**deadline.message_vars, "trigger_uid": str(uuid4())}
            db.update(deadline)
        self.assertEqual(admit_native_notification(deadline.id), 1)
        original = self.events()[0]
        self.expire(original)
        with patch("langboard_shared.domain.services.AppEventDelivery.enqueue_event") as enqueue:
            recover_pending_events()
            _, summary = self.events()
            enqueue.assert_called_once_with(summary.id)
        self.assertTrue(self.authorize(summary)["authorized"])
        self.signed_summary(summary, "shared", 1)
