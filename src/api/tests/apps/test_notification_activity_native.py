"""Native recipient ACL, source revisions and idempotent signed activity admission."""
import json
import unittest
from datetime import timedelta
from unittest.mock import PropertyMock, patch
from test_notification_delivery_native import NotificationDeliveryNativeTest
from test_notification_destination_native import NotificationDestinationNativeTest
from langboard_shared.Env import Env
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Card, CardAssignedUser, CardComment, EmployeeMembershipPolicy, Project, ProjectAssignedUser, ProjectColumn,
    ProjectRole, UserCardNotificationPreference, UserNotification, AppExecutionOutbox, NotificationScheduleRule,
)
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.models.AppNotificationSubscription import AppNotificationSharedProjectScope
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, AppGovernanceConflict
from langboard_shared.domain.services.AppNotificationDelivery import (
    configure_personal_subscription, create_personal_subscription, revoke_personal_subscription, authorize_notification_delivery,
    record_notification_receipt,
)
from langboard_shared.domain.services.AppNotificationActivity import admit_native_notification
from langboard_shared.domain.services.AppEventDelivery import claim_app_event


WIRE_FIXTURES = {}


class NotificationActivityNativeTest(unittest.TestCase):
    connection_revision = NotificationDestinationNativeTest.connection_revision
    challenge = NotificationDestinationNativeTest.challenge
    proof = NotificationDestinationNativeTest.proof
    approve = NotificationDestinationNativeTest.approve
    verified = NotificationDestinationNativeTest.verified

    def setUp(self):
        NotificationDeliveryNativeTest.setUp(self)
        for name, value in (("CARD_INTERNAL_ACCESS_MODE", "project_members"),
                            ("UI_REDIRECT_URL", "https://langboard.example/redirect")):
            mocked = patch.object(type(Env), name, new_callable=PropertyMock, return_value=value)
            mocked.start(); self.addCleanup(mocked.stop)
        for model in (Project, ProjectAssignedUser, ProjectRole, ProjectColumn, Card, CardAssignedUser, CardComment,
                      EmployeeMembershipPolicy, UserCardNotificationPreference, UserNotification, NotificationScheduleRule, AppNotificationSharedProjectScope):
            model.__table__.create(self.engine, checkfirst=True)
        with DbSession.atomic() as db:
            self.project = Project(owner_id=self.owner.id, title="Native notification fixture")
            db.insert(self.project)
            self.column = ProjectColumn(project_id=self.project.id, name="Active", order=0)
            db.insert(self.column)
            db.insert(ProjectAssignedUser(project_id=self.project.id, user_id=self.other.id))
            self.card = Card(project_id=self.project.id, project_column_id=self.column.id, title="Metadata-only activity",
                             visibility="SHARED", created_by_user_id=self.owner.id)
            db.insert(self.card)
            self.preference = UserCardNotificationPreference(user_id=self.owner.id, card_id=self.card.id,
                mode="comments_and_moves", reasons=["creator", "commenter", "mentioned"])
            db.insert(self.preference)
        self.subscription = configure_personal_subscription(self.owner, self.subscription_id, expected_generation=1,
            event_kinds=["card_activity", "card_deadline"], project_uids=[self.project.get_uid()])
        self.generation = self.subscription["generation"]

    def source(self, kind=NotificationType.CardCommentAdded, *, actor=None, comment=None, values=None):
        with DbSession.atomic() as db:
            if kind in {NotificationType.CardCommentAdded, NotificationType.MentionedInComment} and comment is None:
                comment = CardComment(card_id=self.card.id, user_id=self.other.id,
                    content=EditorContentModel(content="private-comment-sentinel-not-exported"))
                db.insert(comment)
            references = [("project", self.project.id), ("card", self.card.id)]
            if comment is not None:
                references.append(("card_comment", comment.id))
            row = UserNotification(notifier_type="user", notifier_id=(actor or self.other).id,
                receiver_id=self.owner.id, notification_type=kind, record_list=references, message_vars=values or {})
            db.insert(row)
        return row, comment

    def events(self):
        with DbSession.atomic() as db:
            return db.exec(SqlBuilder.select.table(AppExecutionOutbox).order_by(AppExecutionOutbox.id)).all()

    def authorize(self, event):
        return authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
            subscription_generation=self.generation, destination_revision=1)

    def deadline(self):
        with DbSession.atomic() as db:
            self.card.deadline_at = SafeDateTime.now() + timedelta(days=1)
            db.update(self.card)
            rule = NotificationScheduleRule(name="Upcoming deadline", is_enabled=True, target="card", field="deadline_at",
                operator="within_next_days", value=7, recipients=["project_owner"])
            db.insert(rule)
        row, _ = self.source(NotificationType.ScheduledRule, actor=self.owner, values={
            "rule_uid": rule.get_uid(), "rule_name": rule.name, "target": "card", "target_table": "card",
            "target_id": int(self.card.id), "field": "deadline_at", "operator": "within_next_days", "value": 7,
            "deadline_at": self.card.deadline_at.isoformat()})
        return row, rule

    def test_native_deadline_predicate_normalizes_database_utc_timestamps(self):
        from types import SimpleNamespace
        from langboard_shared.tasks.notifications.NotificationScheduleTask import _does_rule_match
        now = SafeDateTime.now()
        rule = {"field": "deadline_at", "operator": "within_next_days", "value": 7}
        for clock in (now, now.replace(tzinfo=None)):
            for deadline in (now + timedelta(days=1), (now + timedelta(days=1)).replace(tzinfo=None)):
                self.assertTrue(_does_rule_match(rule, SimpleNamespace(deadline_at=deadline), clock))
            self.assertFalse(_does_rule_match(rule, SimpleNamespace(deadline_at=(now - timedelta(days=1)).replace(tzinfo=None)), clock))

    def test_all_collaboration_types_have_host_owned_content_and_reference(self):
        kinds = [NotificationType.AssignedToCard, NotificationType.MentionedInCard,
                 NotificationType.MentionedInComment, NotificationType.CardCommentAdded, NotificationType.CardMoved]
        for kind in kinds:
            row, _ = self.source(kind, values={"from_column": "Before", "to_column": "After"} if kind == NotificationType.CardMoved else {})
            self.assertEqual(admit_native_notification(row.id), 1, kind.value)
            payload = self.events()[-1].payload
            self.assertEqual(payload["notification"]["source"]["type"], kind.value)
            self.assertTrue(payload["notification"]["title"])
            self.assertTrue(payload["notification"]["body"])
            self.assertIn(self.card.get_uid(), payload["notification"]["url"])
            WIRE_FIXTURES[kind.value] = payload
        deadline, _ = self.deadline()
        self.assertEqual(admit_native_notification(deadline.id), 1)
        WIRE_FIXTURES["card_deadline"] = self.events()[-1].payload

    def test_native_committed_backlog_admits_and_marks_without_another_queue(self):
        import asyncio
        from langboard_shared.infrastructure.repositories import Repository
        from langboard_shared.tasks.notifications.NotificationWorkEventTask import drain_pending_work_events
        row, _ = self.source()
        repository = Repository()
        try:
            asyncio.run(drain_pending_work_events(repository))
            asyncio.run(drain_pending_work_events(repository))
        finally:
            repository.close()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.queue.call_count, 1)
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == row.id)).first()
            self.assertIsNotNone(current.work_event_dispatched_at)

    def test_native_comment_snapshot_is_signed_once_and_never_exports_comment_body(self):
        row, _ = self.source()
        self.assertEqual(admit_native_notification(row.id), 1)
        self.assertEqual(admit_native_notification(row.id), 0)
        self.assertEqual(self.queue.call_count, 1)
        event = self.events()[0]
        payload = event.payload
        self.assertEqual(payload["notification"]["kind"], "card_activity")
        self.assertEqual(payload["notification"]["source"]["card_uid"], self.card.get_uid())
        self.assertEqual(payload["notification"]["reasons"], ["creator", "commenter", "mentioned"])
        self.assertNotIn("private-comment-sentinel", json.dumps(payload))
        self.assertTrue(self.authorize(event)["authorized"])
        signed = claim_app_event(event.id, 1)
        self.assertIsNotNone(signed)
        from langboard_sdk.webhooks import verify_webhook
        verify_webhook(signed["body"], signed["headers"], "test-signing")
        self.assertNotIn("source_fence", json.loads(signed["body"]))

    def test_comment_mention_and_multiple_subscriptions_deduplicate_even_after_revocation(self):
        second = create_personal_subscription(self.owner, self.destination_id, expected_destination_revision=1, request_id="second")
        second_id = SnowflakeID.from_short_code(second["subscription_uid"])
        configure_personal_subscription(self.owner, second_id, expected_generation=1,
            event_kinds=["card_activity"], project_uids=[self.project.get_uid()])
        first, comment = self.source(NotificationType.MentionedInComment)
        ordinary, _ = self.source(comment=comment)
        self.assertEqual(admit_native_notification(first.id), 1)
        self.assertEqual(admit_native_notification(ordinary.id), 0)
        revoke_personal_subscription(self.owner, self.subscription_id, expected_generation=self.generation)
        self.assertEqual(admit_native_notification(first.id), 0)
        self.assertEqual(len(self.events()), 1)

    def test_actor_self_is_suppressed_and_direct_ignore_survives_participation(self):
        row, _ = self.source(actor=self.owner)
        self.assertEqual(admit_native_notification(row.id), 0)
        row, _ = self.source()
        self.assertEqual(admit_native_notification(row.id), 1)
        with DbSession.atomic() as db:
            self.preference.mode = "ignore"; db.update(self.preference)
        with self.assertRaises(AppGovernanceDenied):self.authorize(self.events()[0])
        another, _ = self.source()
        self.assertEqual(admit_native_notification(another.id), 0)

    def test_source_edit_delete_and_current_private_acl_fence_consumer_delivery(self):
        row, comment = self.source()
        admit_native_notification(row.id)
        event = self.events()[0]
        with DbSession.atomic() as db:
            comment.content = EditorContentModel(content="changed source")
            db.update(comment)
        with self.assertRaises(AppGovernanceConflict):self.authorize(event)
        self.assertIsNone(claim_app_event(event.id, 1))
        fresh, _ = self.source()
        self.assertEqual(admit_native_notification(fresh.id), 1)
        with DbSession.atomic() as db:
            self.card.visibility = "PRIVATE"; self.card.owner_user_id = self.owner.id; db.update(self.card)
        with self.assertRaises(AppGovernanceDenied):self.authorize(self.events()[-1])

    def test_private_is_not_exported_and_shared_deadline_requires_current_scope_and_rule(self):
        with DbSession.atomic() as db:
            self.card.visibility = "PRIVATE"; self.card.owner_user_id = self.owner.id; db.update(self.card)
        row, _ = self.source(NotificationType.AssignedToCard)
        self.assertEqual(admit_native_notification(row.id), 0)
        deadline, rule = self.deadline()
        self.assertEqual(admit_native_notification(deadline.id), 0)
        with DbSession.atomic() as db:
            self.card.visibility = "SHARED"; self.card.owner_user_id = None; db.update(self.card)
        deadline, rule = self.deadline()
        self.assertEqual(admit_native_notification(deadline.id), 1)
        event = self.events()[-1]
        self.assertEqual(event.payload["notification"]["kind"], "card_deadline")
        self.assertTrue(self.authorize(event)["authorized"])
        with DbSession.atomic() as db:
            rule.is_enabled = False; db.update(rule)
        with self.assertRaises(AppGovernanceConflict):self.authorize(event)

    def test_deadline_change_archive_and_recipient_change_cancel_pending_send(self):
        row, rule = self.deadline()
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[0]
        with DbSession.atomic() as db:
            self.card.deadline_at += timedelta(days=1); db.update(self.card)
        with self.assertRaises(AppGovernanceConflict):self.authorize(event)
        next_row, rule = self.deadline()
        self.assertEqual(admit_native_notification(next_row.id), 1)
        with DbSession.atomic() as db:
            rule.recipients = ["card_assignees"]; db.update(rule)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):self.authorize(self.events()[-1])
        with DbSession.atomic() as db:
            self.card.archived_at = SafeDateTime.now(); db.update(self.card)
        final, _ = self.deadline()
        self.assertEqual(admit_native_notification(final.id), 0)

    def test_late_deadline_observation_is_retained_without_renewing_send_authority(self):
        row, rule = self.deadline()
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[0]
        self.assertTrue(self.authorize(event)["authorized"])
        # Advance the existing predicate's clock, preserving credential lifetime
        # and all source revisions so this isolates elapsed deadline time.
        future = SafeDateTime.now() + timedelta(days=2)
        from types import SimpleNamespace
        with patch("langboard_shared.domain.services.AppNotificationActivity.SafeDateTime", SimpleNamespace(now=lambda: future)):
            with self.assertRaises(AppGovernanceDenied): self.authorize(event)
            result = record_notification_receipt(self.token["token"], event.id, request_id="late-observation",
                event_revision=1, subscription_generation=self.generation, destination_revision=1,
                status="unknown")
            self.assertEqual(result["receipt"]["status"], "unknown")
            with DbSession.atomic() as db:
                rule.is_enabled = False; db.update(rule)
            with self.assertRaises(AppGovernanceConflict):
                record_notification_receipt(self.token["token"], event.id, request_id="changed-policy",
                    event_revision=1, subscription_generation=self.generation, destination_revision=1, status="unknown")
