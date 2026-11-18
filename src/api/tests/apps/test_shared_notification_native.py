"""Shared room consent and native deadline admission in an isolated database."""
import unittest
from datetime import timedelta
from uuid import uuid4

from test_notification_activity_native import NotificationActivityNativeTest
from test_notification_destination_native import NotificationDestinationNativeTest, IDENTITY
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import AppConnection, Project, UserNotification
from langboard_shared.domain.models.AppNotificationDestination import AppNotificationDestinationChallenge
from langboard_shared.domain.models.AppNotificationSubscription import AppNotificationSharedProjectScope
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.AppNotificationDestinations import (
    create_destination_challenge, revoke_notification_destination, submit_destination_proof,
)
from langboard_shared.domain.services.AppNotificationDelivery import (
    configure_personal_subscription, create_personal_subscription, revoke_personal_subscription,
    authorize_notification_delivery,
)
from langboard_shared.domain.services.AppNotificationActivity import admit_native_notification

ROOM = {"kind": "shared", "external_id": "opaque-room", "display_name": "Room"}


class SharedConsentTest(unittest.TestCase):
    setUp = NotificationDestinationNativeTest.setUp
    connection_revision = NotificationDestinationNativeTest.connection_revision
    proof = NotificationDestinationNativeTest.proof
    approve = NotificationDestinationNativeTest.approve

    def challenge(self, request):
        return create_destination_challenge(self.owner, self.connection.id, request_id=request,
            expected_connection_revision=self.connection_revision(), kind="shared")

    def test_intent_replay_and_explicit_reapproval(self):
        challenge = self.challenge("room")
        with self.assertRaises(AppGovernanceConflict):
            create_destination_challenge(self.owner, self.connection.id, request_id="room",
                expected_connection_revision=self.connection_revision(), kind="personal")
        with self.assertRaises(ValueError):
            self.proof(challenge)
        proof = self.proof(challenge, destination=ROOM)
        self.assertEqual(self.proof(challenge, destination=ROOM), proof)
        first = self.approve(challenge, proof)
        self.assertEqual(first["kind"], "shared")
        revoke_notification_destination(self.owner, SnowflakeID.from_short_code(first["destination_uid"]), expected_revision=1)
        second_challenge = self.challenge("room-again")
        second = self.approve(second_challenge, self.proof(second_challenge, destination=ROOM))
        self.assertEqual(second["destination_uid"], first["destination_uid"])
        self.assertEqual(second["revision"], 3)

    def test_room_owner_cannot_be_replaced_from_other_connection(self):
        first = self.challenge("first")
        self.approve(first, self.proof(first, destination=ROOM))
        with DbSession.atomic() as db:
            connection = AppConnection(app_key=self.connection.app_key, owner_id=self.other.id, state="connected")
            db.insert(connection)
        token = issue_connection_credential(self.other, connection.id)["token"]
        from langboard_shared.domain.services.AppConnectionManagement import list_inbound_connections
        revision = next(row["revision"] for row in list_inbound_connections(self.other, connection.app_key)["items"]
                        if row["connection_uid"] == connection.get_uid())
        challenge = create_destination_challenge(self.other, connection.id, request_id="second",
            expected_connection_revision=revision, kind="shared")
        proof = submit_destination_proof(token, SnowflakeID.from_short_code(challenge["challenge_uid"]),
            challenge_token=challenge["challenge_token"], request_id="proof", external_identity={**IDENTITY, "subject": "other"}, destination=ROOM)
        with self.assertRaises(AppGovernanceConflict):
            self.approve(challenge, proof, actor=self.other)


    def test_same_owner_reapproves_revoked_room_on_another_connection(self):
        first = self.challenge("first")
        destination = self.approve(first, self.proof(first, destination=ROOM))
        revoke_notification_destination(self.owner, SnowflakeID.from_short_code(destination["destination_uid"]), expected_revision=1)
        with DbSession.atomic() as db:
            connection = AppConnection(app_key=self.connection.app_key, owner_id=self.owner.id, state="connected")
            db.insert(connection)
        token = issue_connection_credential(self.owner, connection.id)["token"]
        from langboard_shared.domain.services.AppConnectionManagement import list_inbound_connections
        revision = next(row["revision"] for row in list_inbound_connections(self.owner, connection.app_key)["items"]
                        if row["connection_uid"] == connection.get_uid())
        second = create_destination_challenge(self.owner, connection.id, request_id="new-connection",
            expected_connection_revision=revision, kind="shared")
        proof = submit_destination_proof(token, SnowflakeID.from_short_code(second["challenge_uid"]),
            challenge_token=second["challenge_token"], request_id="proof", external_identity={**IDENTITY, "subject": "rotated"}, destination=ROOM)
        approved = self.approve(second, proof)
        self.assertEqual(destination["destination_uid"], approved["destination_uid"])
        self.assertEqual(approved["connection_uid"], connection.get_uid())

    def test_challenge_proof_kind_mismatch_is_rejected_before_review(self):
        from langboard_shared.domain.models.AppNotificationDestination import AppNotificationDestinationChallenge
        challenge = self.challenge("mismatch")
        with self.assertRaises(ValueError):
            self.proof(challenge, destination={**ROOM, "kind": "personal"})
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
                AppNotificationDestinationChallenge.id == SnowflakeID.from_short_code(challenge["challenge_uid"]))).first()
            self.assertEqual(row.state, "pending")
            self.assertFalse(row.proof)


    def test_revoked_proof_bearer_cannot_approve(self):
        from langboard_shared.domain.services.AppConnectionAuthentication import revoke_connection_credential
        challenge = self.challenge("credential-revoked")
        proof = self.proof(challenge, destination=ROOM)
        revoke_connection_credential(self.owner, self.connection.id, SnowflakeID.from_short_code(self.token["credential_uid"]))
        with self.assertRaises(AppGovernanceDenied):
            self.approve(challenge, proof)


class SharedDeadlineTest(unittest.TestCase):
    setUp = NotificationActivityNativeTest.setUp
    connection_revision = NotificationActivityNativeTest.connection_revision
    challenge = NotificationActivityNativeTest.challenge
    proof = NotificationActivityNativeTest.proof
    approve = NotificationActivityNativeTest.approve
    verified = NotificationActivityNativeTest.verified
    source = NotificationActivityNativeTest.source
    events = NotificationActivityNativeTest.events
    deadline = NotificationActivityNativeTest.deadline

    def shared_subscription(self):
        AppNotificationSharedProjectScope.__table__.create(self.engine, checkfirst=True)
        revoke_personal_subscription(self.owner, self.subscription_id, expected_generation=self.generation)
        challenge = create_destination_challenge(self.owner, self.connection.id, request_id="shared-challenge",
            expected_connection_revision=self.connection_revision(), kind="shared")
        destination = self.approve(challenge, self.proof(challenge, destination=ROOM))
        result = create_personal_subscription(self.owner, SnowflakeID.from_short_code(destination["destination_uid"]),
            expected_destination_revision=1, request_id="shared-subscription", scope="shared", project_uids=[self.project.get_uid()])
        identifier = SnowflakeID.from_short_code(result["subscription_uid"])
        with self.assertRaises(ValueError):
            configure_personal_subscription(self.owner, identifier, expected_generation=1,
                event_kinds=["card_activity"], project_uids=[self.project.get_uid()])
        result = configure_personal_subscription(self.owner, identifier, expected_generation=1,
            event_kinds=["card_deadline"], project_uids=[self.project.get_uid()])
        return identifier, result

    def test_shared_scope_http_is_current_and_bound_to_exact_connection(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.routes.settings import AppNotificationDeliveryApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.domain.models import Project

        identifier, scope = self.shared_subscription()
        route = f"/apps/v1/notification-subscriptions/{scope['subscription_uid']}/scope"
        app = FastAPI()
        app.include_router(AppRouter.api)
        with DbSession.atomic() as db:
            other_connection = AppConnection(app_key=self.connection.app_key, owner_id=self.owner.id, state="connected")
            other_project = Project(owner_id=self.owner.id, title="Unselected board")
            db.insert(other_connection)
            db.insert(other_project)
            db.insert(AppNotificationSharedProjectScope(subscription_id=identifier, project_id=other_project.id))
        other_token = issue_connection_credential(self.owner, other_connection.id)["token"]
        headers = {"authorization": "Bearer " + self.token["token"]}
        with TestClient(app) as client:
            self.assertEqual(client.get(route).status_code, 401)
            self.assertEqual(client.get(route, headers={"authorization": "Bearer " + other_token}).status_code, 403)
            response = client.get(route, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), {**scope, "connection_uid": self.connection.get_uid()})
            self.assertNotIn("opaque-room", response.text)
            with DbSession.atomic() as db:
                self.project.owner_id = self.other.id
                db.update(self.project)
            withdrawn = client.get(route, headers=headers)
            self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
            self.assertEqual(withdrawn.json()["project_uids"], [])
            revoke_personal_subscription(self.owner, identifier, expected_generation=scope["generation"])
            self.assertEqual(client.get(route, headers=headers).status_code, 403)

    def test_challenge_scope_http_rejects_foreign_credentials_and_stale_board_access(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.routes.settings import AppNotificationDestinationApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.domain.services.AppNotificationDestinations import read_destination_challenge

        with DbSession.atomic() as db:
            project = Project(owner_id=self.owner.id, title="Another readable board")
            db.insert(project)
            foreign = [AppConnection(app_key=self.connection.app_key, owner_id=user.id, state="connected")
                       for user in (self.owner, self.other)]
            for connection in foreign:
                db.insert(connection)
        foreign_tokens = [issue_connection_credential(user, connection.id)["token"]
                          for user, connection in zip((self.owner, self.other), foreign)]
        params = dict(expected_connection_revision=self.connection_revision(), kind="shared")
        def create(key, board):
            return create_destination_challenge(
                self.owner, self.connection.id, request_id=key, project_uid=board, **params)

        challenge = create("scope", self.project.get_uid())
        expired = create("expired-scope", self.project.get_uid())
        identifier = SnowflakeID.from_short_code(challenge["challenge_uid"])
        with self.assertRaises(AppGovernanceConflict):
            create("scope", project.get_uid())
        with self.assertRaises(AppGovernanceDenied):
            read_destination_challenge(self.other, identifier)
        self.assertNotIn("challenge_token", create("scope", self.project.get_uid()))
        personal = self.challenge("personal-scope")
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
                AppNotificationDestinationChallenge.id == SnowflakeID.from_short_code(expired["challenge_uid"]))).first()
            row.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(row)
        app = FastAPI()
        app.include_router(AppRouter.api)
        bearer = self.token["token"]

        with TestClient(app) as client:
            def read(item, token=bearer, nonce=None):
                return client.post(f"/apps/v1/notification-destination-challenges/{item['challenge_uid']}/scope",
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    json={"challenge_token": nonce or item["challenge_token"]})

            response = read(challenge)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), {"challenge_uid": challenge["challenge_uid"], "connection_uid": self.connection.get_uid(),
                                               "project_uid": self.project.get_uid(), "expires_at": challenge["expires_at"]})
            self.assertEqual(read(challenge, token=None).status_code, 401)
            for token in foreign_tokens:
                self.assertEqual(read(challenge, token).status_code, 403)
            for rejected in (personal, expired):
                self.assertEqual(read(rejected).status_code, 403)
            self.assertEqual(read(challenge, nonce="z" * 43).status_code, 403)
            proof = self.proof(challenge, destination=ROOM)
            destination = self.approve(challenge, proof)
            self.assertEqual(read(challenge).status_code, 403)
            with self.assertRaises(AppGovernanceConflict):
                create_personal_subscription(self.owner, SnowflakeID.from_short_code(destination["destination_uid"]),
                    expected_destination_revision=1, request_id="switch", scope="shared", project_uids=[project.get_uid()])
            pending = create("withdrawn", self.project.get_uid())
            proof = self.proof(pending, destination={**ROOM, "external_id": "other-room"})
            with DbSession.atomic() as db:
                self.project.owner_id = self.other.id
                db.update(self.project)
            self.assertEqual(read(pending).status_code, 403)
            with self.assertRaises(AppGovernanceDenied):
                self.approve(pending, proof)
            with self.assertRaises(AppGovernanceDenied):
                create("denied-create", self.project.get_uid())

    def test_one_room_dispatch_per_firing_and_source_revalidation(self):
        identifier, subscription = self.shared_subscription()
        first, rule = self.deadline()
        trigger = str(uuid4())
        with DbSession.atomic() as db:
            first.message_vars = {**first.message_vars, "trigger_uid": trigger}; db.update(first)
            second = UserNotification(notifier_type=first.notifier_type, notifier_id=first.notifier_id,
                receiver_id=self.other.id, notification_type=NotificationType.ScheduledRule,
                record_list=first.record_list, message_vars=first.message_vars)
            db.insert(second)
        self.assertEqual(admit_native_notification(first.id), 1)
        self.assertEqual(admit_native_notification(second.id), 0)
        event = self.events()[-1]
        self.assertEqual(event.payload["external_destination"]["kind"], "shared")
        import test_notification_activity_native
        test_notification_activity_native.WIRE_FIXTURES["shared_card_deadline"] = event.payload
        self.assertTrue(authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
            subscription_generation=subscription["generation"], destination_revision=1)["authorized"])
        with DbSession.atomic() as db:
            self.card.deadline_at += timedelta(days=1); db.update(self.card)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)
        third, _ = self.deadline()
        with DbSession.atomic() as db:
            third.message_vars = {**third.message_vars, "trigger_uid": str(uuid4())}; db.update(third)
        self.assertEqual(admit_native_notification(third.id), 1)

    def test_missing_trigger_private_and_archived_cards_denied(self):
        self.shared_subscription()
        row, _ = self.deadline()
        self.assertEqual(admit_native_notification(row.id), 0)
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
            self.card.visibility = "PRIVATE"; self.card.owner_user_id = self.owner.id; db.update(self.card)
        self.assertEqual(admit_native_notification(row.id), 0)
        with DbSession.atomic() as db:
            self.card.visibility = "SHARED"; self.card.owner_user_id = None
            self.card.archived_at = SafeDateTime.now(); db.update(self.card)
        self.assertEqual(admit_native_notification(row.id), 0)

    def test_revoked_consent_blocks_delivery_and_unselected_project_is_not_admitted(self):
        identifier, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        destination_id = SnowflakeID.from_short_code(subscription["destination_uid"])
        revoke_notification_destination(self.owner, destination_id, expected_revision=1)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)

    def test_scope_index_and_current_selection_must_both_match(self):
        identifier, _ = self.shared_subscription()
        with DbSession.atomic() as db:
            index = db.exec(SqlBuilder.select.table(AppNotificationSharedProjectScope).where(
                AppNotificationSharedProjectScope.subscription_id == identifier)).first()
            self.assertEqual(index.project_id, self.project.id)
            db.delete(index)
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 0)


    def test_exact_selected_board_and_changed_scope_fence(self):
        from langboard_shared.domain.models import Project
        identifier, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
            other_project = Project(owner_id=self.owner.id, title="Other board")
            db.insert(other_project)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        changed = configure_personal_subscription(self.owner, identifier,
            expected_generation=subscription["generation"], event_kinds=["card_deadline"],
            project_uids=[other_project.get_uid()])
        self.assertEqual(changed["project_uids"], [other_project.get_uid()])
        with DbSession.atomic() as db:
            entries = db.exec(SqlBuilder.select.table(AppNotificationSharedProjectScope).where(
                AppNotificationSharedProjectScope.subscription_id == identifier)).all()
            self.assertEqual([entry.project_id for entry in entries], [other_project.id])
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)
        fresh, _ = self.deadline()
        with DbSession.atomic() as db:
            fresh.message_vars = {**fresh.message_vars, "trigger_uid": str(uuid4())}; db.update(fresh)
        self.assertEqual(admit_native_notification(fresh.id), 0)

    def test_deactivated_owner_denies_pending_room_delivery(self):
        _, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        with DbSession.atomic() as db:
            self.owner.activated_at = None; db.update(self.owner)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)

    def test_provider_receipt_replay_and_generation_fence(self):
        from langboard_shared.domain.services.AppNotificationDelivery import record_notification_receipt
        identifier, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        kwargs = dict(request_id="receipt", event_revision=1, subscription_generation=subscription["generation"],
                      destination_revision=1, status="adapter_accepted")
        first = record_notification_receipt(self.token["token"], event.id, **kwargs)
        self.assertEqual(record_notification_receipt(self.token["token"], event.id, **kwargs), first)
        with self.assertRaises(AppGovernanceConflict):
            record_notification_receipt(self.token["token"], event.id, **{**kwargs, "status": "unknown"})
        revoke_personal_subscription(self.owner, identifier, expected_generation=subscription["generation"])
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            record_notification_receipt(self.token["token"], event.id, **kwargs)

    def test_indexed_fanout_overflow_preserves_source(self):
        from unittest.mock import patch
        self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        with patch("langboard_shared.domain.services.AppNotificationActivity.MAX_SUBSCRIPTIONS", 0):
            with self.assertRaises(ValueError):
                admit_native_notification(row.id)
        self.assertEqual(len(self.events()), 0)
        with DbSession.atomic() as db:
            retained = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == row.id)).first()
            self.assertIsNotNone(retained)
        self.assertEqual(admit_native_notification(row.id), 1)

    def test_shared_owner_need_not_be_native_rule_recipient(self):
        self.shared_subscription()
        row, rule = self.deadline()
        with DbSession.atomic() as db:
            rule.recipients = ["card_assignees"]; db.update(rule)
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)

    def test_changed_rule_and_deleted_index_block_delivery_or_admission(self):
        _, subscription = self.shared_subscription()
        row, rule = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        with DbSession.atomic() as db:
            rule.is_enabled = False; db.update(rule)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)


    def test_new_bearer_can_authorize_mature_consent_after_old_bearer_revoked(self):
        from langboard_shared.domain.services.AppConnectionAuthentication import revoke_connection_credential
        _, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        new_token = issue_connection_credential(self.owner, self.connection.id)
        revoke_connection_credential(self.owner, self.connection.id, SnowflakeID.from_short_code(self.token["credential_uid"]))
        self.assertTrue(authorize_notification_delivery(new_token["token"], event.id, event_revision=1,
            subscription_generation=subscription["generation"], destination_revision=1)["authorized"])
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)

    def test_lookup_queries_only_the_actual_project_and_activity_avoids_index(self):
        from sqlalchemy import event
        self.shared_subscription()
        statements = []
        def capture(_conn, _cursor, statement, parameters, _context, _many):
            if "app_notification_shared_project_scope" in statement.lower():
                statements.append((statement, parameters))
        event.listen(self.engine, "before_cursor_execute", capture)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", capture)
        activity, _ = self.source()
        self.assertEqual(admit_native_notification(activity.id), 0)
        self.assertEqual(statements, [])
        deadline, _ = self.deadline()
        with DbSession.atomic() as db:
            deadline.message_vars = {**deadline.message_vars, "trigger_uid": str(uuid4())}; db.update(deadline)
        self.assertEqual(admit_native_notification(deadline.id), 1)
        self.assertTrue(any("project_id" in sql.lower() and int(self.project.id) in params
                            for sql, params in statements))


    def test_departed_board_member_cannot_authorize_room_delivery(self):
        from langboard_shared.domain.models import ProjectAssignedUser, ProjectRole
        with DbSession.atomic() as db:
            self.project.owner_id = self.other.id; db.update(self.project)
            membership = ProjectAssignedUser(project_id=self.project.id, user_id=self.owner.id)
            db.insert(membership)
            db.insert(ProjectRole(project_id=self.project.id, user_id=self.owner.id, actions=["read"]))
        _, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        with DbSession.atomic() as db:
            db.delete(membership)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)

    def test_mutated_native_source_invalidates_delivery(self):
        _, subscription = self.shared_subscription()
        row, _ = self.deadline()
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        self.assertEqual(admit_native_notification(row.id), 1)
        event = self.events()[-1]
        with DbSession.atomic() as db:
            row.message_vars = {**row.message_vars, "trigger_uid": str(uuid4())}; db.update(row)
        with self.assertRaises((AppGovernanceConflict, AppGovernanceDenied)):
            authorize_notification_delivery(self.token["token"], event.id, event_revision=1,
                subscription_generation=subscription["generation"], destination_revision=1)


class ScheduleTriggerTest(unittest.TestCase):
    def test_trigger_is_shared_by_recipients_and_changes_on_new_firing(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from langboard_shared.tasks.notifications.NotificationScheduleTask import _run_notification_rules_with_resources
        project = SimpleNamespace(owner_id=1)
        class Target:
            __tablename__ = "card"
            id = 2
        target = Target()
        candidate = {"project": project, "target_model": target}
        service = SimpleNamespace(notification=SimpleNamespace(notify_notification_schedule_rule=Mock(return_value=True)))
        rule = {"uid": "rule", "target": "card", "field": "deadline_at"}
        module = "langboard_shared.tasks.notifications.NotificationScheduleTask."
        with patch(module + "_get_rule_uid", return_value="rule"), \
             patch(module + "_get_rule_notification_type", return_value=NotificationType.ScheduledRule), \
             patch(module + "_get_recent_scheduled_rule_notification_keys", return_value=set()), \
             patch(module + "_get_scheduled_rule_candidates", return_value=[candidate]), \
             patch(module + "_resolve_rule_recipients", return_value=[SimpleNamespace(id=3), SimpleNamespace(id=4)]), \
             patch(module + "InfraHelper.get_by_id_like", return_value=SimpleNamespace(id=1)):
            _run_notification_rules_with_resources([rule], service, None)
            _run_notification_rules_with_resources([rule], service, None)
        calls = service.notification.notify_notification_schedule_rule.call_args_list
        triggers = [call.args[6]["trigger_uid"] for call in calls]
        self.assertEqual(triggers[0], triggers[1])
        self.assertEqual(triggers[2], triggers[3])
        self.assertNotEqual(triggers[0], triggers[2])


class SharedMigrationTest(unittest.TestCase):
    def test_upgrade_preserves_personal_rows_and_refuses_shared_history_loss(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import create_engine, inspect, text
        from sqlalchemy.exc import IntegrityError
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        versions = Path(__file__).resolve().parents[2] / "langboard/migrations/versions"
        def load(name):
            spec = importlib.util.spec_from_file_location(name, versions / name)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module
        board_scope = load("20261119034000-57742a932cb1.py")
        original = load("20261116202430-c4f143949d80.py")
        current = load("20261118120336-b40c2487826e.py")
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            original.op = current.op = board_scope.op = Operations(MigrationContext.configure(connection))
            original.upgrade()
            connection.execute(text("INSERT INTO app_notification_destination_challenge (id,user_id,connection_id,request_id,app_key,connection_identity,token_hash,state,revision,proof) VALUES (1,2,3,'retained','test','identity','hash','pending',1,'{}')"))
            current.upgrade()
            self.assertEqual(connection.execute(text("SELECT kind FROM app_notification_destination_challenge WHERE id=1")).scalar_one(), "personal")
            self.assertIn("ix_app_notification_shared_project_scope_project_id", {item["name"] for item in inspect(connection).get_indexes("app_notification_shared_project_scope")})
            with self.assertRaises(IntegrityError), connection.begin_nested():
                connection.execute(text("UPDATE app_notification_destination_challenge SET kind='invalid' WHERE id=1"))
            current.downgrade()
            self.assertEqual(connection.execute(text("SELECT request_id FROM app_notification_destination_challenge WHERE id=1")).scalar_one(), "retained")
            current.upgrade()
            connection.execute(text("UPDATE app_notification_destination_challenge SET kind='shared' WHERE id=1"))
            with self.assertRaisesRegex(RuntimeError, "retained shared"):
                current.downgrade()
            self.assertEqual(connection.execute(text("SELECT kind FROM app_notification_destination_challenge WHERE id=1")).scalar_one(), "shared")
            board_scope.upgrade()
            self.assertIsNone(connection.execute(text("SELECT project_id FROM app_notification_destination_challenge WHERE id=1")).scalar_one())
            board_scope.downgrade()
            board_scope.upgrade()
            connection.execute(text("UPDATE app_notification_destination_challenge SET project_id=8 WHERE id=1"))
            with self.assertRaisesRegex(RuntimeError, "retained notification challenge board scope"):
                board_scope.downgrade()
            self.assertEqual(connection.execute(text("SELECT project_id FROM app_notification_destination_challenge WHERE id=1")).scalar_one(), 8)
