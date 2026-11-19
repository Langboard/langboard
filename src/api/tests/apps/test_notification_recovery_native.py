"""Recover expired, unsent native notifications through the existing event queue."""

import unittest
from asyncio import run
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from langboard_sdk.notification_events import parse_notification_event
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import AppExecutionOutbox
from langboard_shared.domain.models.AppNotificationSubscription import AppNotificationDispatch
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, recover_pending_events
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current
from langboard_shared.domain.services.AppNotificationActivity import admit_native_notification
from langboard_shared.domain.services.AppNotificationDelivery import configure_personal_subscription
from langboard_shared.tasks.webhooks import AppEventDeliveryWorker

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
        if audience == "shared":
            self.assertEqual(payload["project_uid"], self.project.get_uid())
        else:
            self.assertNotIn("project_uid", payload)
        notification = payload["notification"]
        self.assertEqual(notification["kind"], "missed_summary")
        self.assertEqual(notification["count"], count)
        self.assertEqual(set(notification), {"kind", "title", "body", "count", "window_start", "window_end"})
        self.assertNotIn(self.card.get_uid(), signed["body"].decode())
        self.assertNotIn("missed_event_uids", payload)
        WIRE_FIXTURES[audience + "_missed_summary"] = payload

    def enable_summaries(self, identifier, subscription):
        self.generation = configure_personal_subscription(
            self.owner, identifier, expected_generation=subscription["generation"],
            event_kinds=[*subscription["event_kinds"], "missed_summary"],
            project_uids=subscription["project_uids"],
        )["generation"]

    def test_existing_receivers_do_not_implicitly_opt_in(self):
        source, _ = self.source()
        self.assertEqual(admit_native_notification(source.id), 1)
        original = self.events()[0]
        self.expire(original)
        with patch("langboard_shared.domain.services.AppEventDelivery.enqueue_event") as enqueue:
            recover_pending_events()
            enqueue.assert_called_once_with(original.id)
        self.assertEqual(len(self.events()), 1)

    def test_scheduled_recovery_delivers_one_signed_summary_instead_of_expired_originals(self):
        self.enable_summaries(self.subscription_id, self.subscription)
        for _ in range(2):
            source, _ = self.source()
            self.assertEqual(admit_native_notification(source.id), 1)
        originals = self.events()
        for original in originals:
            self.expire(original)

        with (
            patch.object(AppEventDeliveryWorker, "ensure_public_webhook_url", return_value="resolved-test-target"),
            patch.object(AppEventDeliveryWorker, "post_resolved_webhook_bytes") as post,
        ):
            self.assertEqual(run(AppEventDeliveryWorker.recover_app_events()), 1)
            self.assertEqual(run(AppEventDeliveryWorker.recover_app_events()), 0)
            post.assert_awaited_once()
            _, body, headers = post.await_args.args
            event = parse_notification_event(body, headers, "test-signing",
                expected_app_key=self.connection.app_key, expected_connection_uid=self.connection.get_uid())
        self.assertEqual((event["notification"]["kind"], event["notification"]["count"]), ("missed_summary", 2))
        self.assertEqual([(row.state, row.attempt_count) for row in self.events()],
            [("blocked", 0), ("blocked", 0), ("delivered", 1)])

    def test_late_individual_claim_leaves_expired_opted_in_event_for_recovery(self):
        self.enable_summaries(self.subscription_id, self.subscription)
        source, _ = self.source()
        self.assertEqual(admit_native_notification(source.id), 1)
        original = self.events()[0]
        self.expire(original)
        self.assertIsNone(claim_app_event(original.id, 1))
        self.assertEqual((self.events()[0].state, self.events()[0].attempt_count), ("pending", 0))

    def test_only_zero_attempt_receipt_free_sources_are_summarized_once(self):
        self.enable_summaries(self.subscription_id, self.subscription)
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
        identifier, subscription = self.shared_subscription()
        self.enable_summaries(identifier, subscription)
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
        with DbSession.atomic() as db:
            current = _current(db, AppExecutionOutbox, summary.id)
            dispatch = _current(db, AppNotificationDispatch, current.request_id)
            wrong_project_uid = self.card.get_uid()
            current.payload = {**current.payload, "project_uid": wrong_project_uid}
            dispatch.snapshot = {**dispatch.snapshot, "payload": current.payload}
            db.update(current)
            db.update(dispatch)
        with self.assertRaises(AppGovernanceConflict):
            self.authorize(summary)

    def test_shared_subscription_cannot_opt_into_or_receive_private_deadlines(self):
        identifier, shared = self.shared_subscription()
        project_uid = self.project.get_uid()
        with self.assertRaises(ValueError):
            configure_personal_subscription(self.owner, identifier, expected_generation=shared["generation"],
                event_kinds=["card_deadline"], project_uids=[project_uid], private_deadline_project_uids=[project_uid])
        self.enable_summaries(identifier, shared)
        with DbSession.atomic() as db:
            self.card.visibility = "PRIVATE"
            self.card.owner_user_id = self.owner.id
            db.update(self.card)
        deadline, _ = self.deadline()
        with DbSession.atomic() as db:
            deadline.message_vars = {**deadline.message_vars, "trigger_uid": str(uuid4())}
            db.update(deadline)
        self.assertEqual(admit_native_notification(deadline.id), 0)
        self.assertEqual(self.events(), [])
