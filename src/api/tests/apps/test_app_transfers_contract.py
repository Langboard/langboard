"""Dependency-free service contract checks; database/route integration is separate.

Execute this file directly with Python. Actual candidate functions are compiled,
with infrastructure imports replaced by explicit transaction and authority seams.
No SQL isolation, migration, browser or provider acceptance is claimed.
"""

import ast
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from uuid import UUID


class Denied(Exception):
    pass


class Conflict(Exception):
    pass


class Imports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None


class TransferTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parents[3] / "shared/py/langboard_shared/domain/services/AppTransfers.py"
        self.g = {"deepcopy": deepcopy, "sha256": sha256, "BytesIO": BytesIO, "UUID": UUID, "AppGovernanceDenied": Denied,
                  "AppGovernanceConflict": Conflict, "SafeDateTime": NS(now=lambda: datetime.now(timezone.utc))}
        tree = Imports().visit(ast.parse(source.read_text()))
        exec(compile(ast.fix_missing_locations(tree), str(source), "exec"), self.g)
        self.real_scope = self.g["_scope"]
        self.row = NS(id=1, actor_id=7, project_id=2, card_id=3, connection_id=4, app_key="app",
                      client_request_id="00000000-0000-0000-0000-000000000001",
                      get_uid=lambda: "request", intent={"card_revision": 9, "resource_uid": "resource",
                          "attachment_uid": None, "attachment_revision": None, "content_sha256": None},
                      authority={"policy_revision": "p1", "destination": {"revision": 1}},
                      receipt={"revision": 0, "retry_revision": 0, "acknowledgment": None, "provider_outcome": None, "history": []})
        self.event = NS(request_id=1, project_id=2, connection_id=4, app_key="app", get_uid=lambda: "event",
                        state="failed", attempt_count=4, last_error="delivery_failed", delivery_history=[{"attempt": 4}])
        self.card = NS(id=3, last_change_seq=9, get_uid=lambda: "card", description=NS(content="body"), title="Title")
        self.actor = NS(id=7)
        class UID(int):
            def to_short_code(self):
                return {2: "board", 4: "connection"}[self]
            @staticmethod
            def from_short_code(value):
                return value
        self.g.update(SnowflakeID=UID, AppTransferRequest=object(), User=object(), CardAttachment=object(),
                      lock_policy_mutation=lambda db, **kw: None, _lock_transfer_request=lambda *a: None, _actor=lambda db, actor, **kw: actor,
                      _scope=Mock(return_value=(self.card, self.row.authority)),
                      _current=Mock(side_effect=lambda db, model, uid, **kw: self.row if uid == 1 else self.actor),
                      APP_TRANSFER_EVENT="io.langboard.app.transfer.requested.v1")
        self.event.payload = {"schema_version": 1, "event_uid": "event", "event_type": self.g["APP_TRANSFER_EVENT"],
                              "request_uid": "request", "board_uid": "board", "card_uid": "card", "app_key": "app",
                              "connection_uid": "connection", **self.row.intent, "destination": self.row.authority["destination"], "started": False}
        self.db = NS(update=Mock(), after_commit=Mock())
        self.g["DbSession"] = NS(atomic=lambda: nullcontext(self.db))

    def test_real_scope_checks_both_acls_and_explicit_capability_and_resource_identity(self):
        class Field:
            def __eq__(self, other): return True
        definition_type = type("Definition", (), {"key": Field()})
        binding_type = type("Binding", (), {"project_id": Field(), "app_key": Field()})
        card_type, user_type, resource_type = object(), object(), object()
        project = NS(id=2, organization_id=None)
        self.card.archived_at = None;self.card.source_type = None
        connection = NS(id=4, app_key="app", owner_id=7)
        definition = NS(is_enabled=True, declaration={"capabilities": ["transfers.receive", "cards.read", "resources.read", "events.receive"],
                            "resource_types": ["folder"]}, edit_revision=lambda: "definition-revision")
        binding = NS(id=5, state="enabled", granted_capabilities=list(definition.declaration["capabilities"]), edit_revision=lambda: "binding-revision")
        resource = NS(connection_id=4, board_binding_id=5, is_selected=True, access_state="granted", resource_type="folder",
                      identity_hash="identity-v1", access_revision=1)
        readable = Mock(return_value=(project, self.card, None))
        self.g.update(AppDefinition=definition_type, BoardAppBinding=binding_type, Card=card_type, User=user_type,
                      AppResourceBinding=resource_type, ProjectRoleAction=NS(CardUpdate="card_update"),
                      _authorized_board=lambda *a, **kw: project, _connection=lambda *a: connection,
                      require_connection_access=Mock(), DomainService=lambda: NS(card=NS(resolve_readable_card=readable)),
                      _one=lambda db, model, *a: definition if model is definition_type else binding,
                      _identity_hash=lambda *a, **kw: "identity-v1", current_destination=lambda *a: (NS(get_uid=lambda: "destination", revision=1), None),
                      current_policy=lambda *a: {"revision": "policy-generation-1"},
                      _current=lambda db, model, identifier, **kw: self.card if model is card_type else self.actor if model is user_type else resource)
        args = (self.db, self.actor, 2, 3, "app", 4, "resource")
        _, authority = self.real_scope(*args)
        self.assertEqual(readable.call_count, 2)
        self.assertEqual(self.g["require_connection_access"].call_count, 2)
        self.assertEqual(authority["policy_revision"], "policy-generation-1")
        for answers in ([None], [(project, self.card, None), None]):
            readable.side_effect = answers
            with self.assertRaises(Denied): self.real_scope(*args)
        readable.side_effect = None
        for model, key, changed in ((definition, "is_enabled", False), (binding, "state", "disabled"),
                                    (binding, "granted_capabilities", ["cards.read", "resources.read", "events.receive"]),
                                    (resource, "access_state", "revoked"), (resource, "identity_hash", "old-connection"),
                                    (resource, "connection_id", 99), (resource, "is_selected", False)):
            original = getattr(model, key);setattr(model, key, changed)
            with self.subTest(key=key), self.assertRaises(Denied): self.real_scope(*args)
            setattr(model, key, original)
        self.g["require_connection_access"].side_effect = Denied("revoked")
        with self.assertRaises(Denied): self.real_scope(*args)

    def test_revalidation_rejects_authority_aba_and_revision_changes(self):
        for changed in ("policy_revision", "connection_identity", "app_revision", "binding_revision", "resource_revision", "destination"):
            with self.subTest(changed=changed):
                self.g["_scope"].return_value = (self.card, {**self.row.authority, changed: "changed"})
                with self.assertRaises(Conflict): self.g["revalidate_transfer_event"](self.db, self.event)
        self.g["_scope"].return_value = (self.card, self.row.authority)
        self.card.last_change_seq = 10
        with self.assertRaises(Conflict): self.g["revalidate_transfer_event"](self.db, self.event)

    def test_revalidation_rejects_current_sender_or_app_acl_and_revoked_connection(self):
        for boundary in ("sender ACL", "app principal ACL", "connection revoked", "resource revoked"):
            with self.subTest(boundary=boundary):
                self.g["_scope"].side_effect = Denied(boundary)
                with self.assertRaises(Denied): self.g["revalidate_transfer_event"](self.db, self.event)
        self.assertEqual(self.event.attempt_count, 4)

    def test_event_cannot_substitute_another_card_connection_or_content(self):
        original = dict(self.event.payload)
        for key in ("card_uid", "connection_uid", "request_uid", "card_revision", "resource_uid", "destination", "event_uid"):
            with self.subTest(key=key):
                self.event.payload = {**original, key: "forged"}
                with self.assertRaises(Conflict): self.g["revalidate_transfer_event"](self.db, self.event)

    def test_attachment_card_revision_hash_replacement_and_deletion(self):
        attachment = NS(card_id=3, deleted_at=None, replaced_by_id=None, image_revision="revision", content_sha256="hash")
        self.g["_current"] = lambda *a, **kw: attachment
        intent = {"attachment_uid": "attachment", "attachment_revision": "revision", "content_sha256": "hash"}
        self.assertIs(self.g["_attachment"](self.db, self.card, intent), attachment)
        for key, value, error in (("card_id", 99, Denied), ("deleted_at", "deleted", Denied),
                                  ("replaced_by_id", 10, Denied), ("image_revision", "new", Conflict),
                                  ("content_sha256", "new", Conflict)):
            old = getattr(attachment, key);setattr(attachment, key, value)
            with self.subTest(key=key), self.assertRaises(error): self.g["_attachment"](self.db, self.card, intent)
            setattr(attachment, key, old)

    def test_attachment_bytes_verified_and_bounded_without_external_url(self):
        digest = sha256(b"content").hexdigest()
        self.row.intent["content_sha256"] = digest
        self.g["_app_scope"] = lambda *a: (self.row, self.event, self.card)
        attachment = NS(image_revision="revision", content_sha256=digest, file=NS(storage_type="local", storage_name="card", filename="object", path="https://untrusted.invalid/not-fetched"))
        self.g["_attachment"] = lambda *a: attachment
        def download(file, sink): sink.write(b"content");return True
        self.g["Storage"] = NS(download_file=download)
        self.assertEqual(self.g["read_transfer_attachment"]("token", 2, 1), (b"content", digest))
        self.row.intent["content_sha256"] = "wrong"
        with self.assertRaises(Conflict): self.g["read_transfer_attachment"]("token", 2, 1)
        sink = self.g["_BoundedBytes"]()
        with self.assertRaises(ValueError): sink.write(b"x" * (self.g["MAX_ATTACHMENT_BYTES"] + 1))

    def test_storage_race_discards_bytes_after_revocation_or_replacement(self):
        digest = sha256(b"content").hexdigest()
        self.row.intent["content_sha256"] = digest
        attachment = NS(image_revision="revision", content_sha256=digest,
                        file=NS(storage_type="local", storage_name="card", filename="object"))
        self.g["_attachment"] = lambda *a: attachment
        calls = []
        self.g["_app_scope"] = lambda *a: (calls.append("authorize") or (self.row, self.event, self.card))
        def replace(file, sink):
            calls.append("io");sink.write(b"content");attachment.image_revision = "replacement";return True
        self.g["Storage"] = NS(download_file=replace)
        with self.assertRaises(Conflict): self.g["read_transfer_attachment"]("token", 2, 1)
        self.assertEqual(calls, ["authorize", "io", "authorize"])
        attachment.image_revision = "revision"
        authorized = Mock(side_effect=[(self.row, self.event, self.card), Denied("revoked during read")])
        self.g["_app_scope"] = authorized
        self.g["Storage"] = NS(download_file=lambda file, sink: bool(sink.write(b"content")))
        with self.assertRaises(Denied): self.g["read_transfer_attachment"]("token", 2, 1)
        self.assertEqual(authorized.call_count, 2)

    def test_app_ack_does_not_change_transport_or_execution(self):
        self.g["_app_scope"] = lambda *a: (self.row, self.event, self.card)
        result = self.g["report_transfer"]("token", 2, 1, 0, "received", "unknown")
        self.assertEqual(self.event.state, "failed")
        self.assertEqual(result["receipt"]["acknowledgment"], "received")
        self.assertEqual(result["receipt"]["provider_outcome"], "unknown")
        self.assertIs(result["started"], False)
        replay = self.g["report_transfer"]("token", 2, 1, 0, "received", "unknown")
        self.assertFalse(replay["changed"])
        with self.assertRaises(Conflict): self.g["report_transfer"]("token", 2, 1, 0, "received", "delivered")
        self.g["report_transfer"]("token", 2, 1, 1, "received", "delivered")
        with self.assertRaises(Conflict): self.g["report_transfer"]("token", 2, 1, 2, "received", "failed")

    def test_retry_budget_uses_retained_revision_without_resetting_attempts(self):
        self.g["_current"] = lambda *a: self.row
        self.assertEqual(self.g["transfer_attempt_limit"](self.db, self.event, 4), 4)
        self.row.receipt["retry_revision"] = 100
        self.assertEqual(self.g["transfer_attempt_limit"](self.db, self.event, 4), 404)
        self.assertEqual(self.event.attempt_count, 4)
        self.assertEqual(self.event.delivery_history, [{"attempt": 4}])

    def retry_seams(self):
        class Field:
            def __eq__(self, other): return True
        self.g["AppTransferRequest"] = type("Request", (), {name: Field() for name in ("actor_id", "client_request_id", "project_id")})
        self.g["_one"] = lambda *a, **kw: self.row
        self.g["_event"] = lambda *a: self.event
        self.g["revalidate_transfer_event"] = Mock(return_value=(self.row, self.card))
        self.g["enqueue_event"] = Mock()

    def test_retry_is_idempotent_fenced_and_preserves_history(self):
        self.retry_seams()
        result = self.g["retry_transfer"](self.actor, 2, self.row.client_request_id, 0)
        self.assertTrue(result["changed"])
        self.assertEqual(self.row.receipt["retry_revision"], 1)
        self.assertEqual(self.event.attempt_count, 4)
        self.assertEqual(self.event.delivery_history, [{"attempt": 4}])
        self.assertEqual(self.db.after_commit.call_count, 1)
        duplicate = self.g["retry_transfer"](self.actor, 2, self.row.client_request_id, 0)
        self.assertFalse(duplicate["changed"])
        self.assertEqual(self.db.after_commit.call_count, 1)
        with self.assertRaises(Conflict): self.g["retry_transfer"](self.actor, 2, self.row.client_request_id, 2)

    def test_retry_cannot_bypass_revocation_or_app_receipt(self):
        self.retry_seams()
        self.g["revalidate_transfer_event"].side_effect = Denied()
        with self.assertRaises(Denied): self.g["retry_transfer"](self.actor, 2, self.row.client_request_id, 0)
        self.db.after_commit.assert_not_called()
        self.g["revalidate_transfer_event"].side_effect = None
        self.row.receipt["acknowledgment"] = "received"
        with self.assertRaises(Conflict): self.g["retry_transfer"](self.actor, 2, self.row.client_request_id, 0)
        self.assertEqual(self.row.receipt["retry_revision"], 0)

    def test_duplicate_create_reconciles_same_identity_and_rejects_payload_reuse(self):
        self.retry_seams()
        args = (self.actor, 2, 3, "app", self.row.client_request_id, 4, dict(self.row.intent), dict(self.row.authority))
        result = self.g["create_transfer"](*args)
        self.assertFalse(result["changed"])
        self.assertEqual(result["delivery"]["event_uid"], "event")
        self.db.after_commit.assert_not_called()
        changed = list(args);changed[6] = {**self.row.intent, "card_revision": 10}
        with self.assertRaises(Conflict): self.g["create_transfer"](*changed)
        changed = list(args);changed[7] = {"policy_revision": "changed"}
        with self.assertRaises(Conflict): self.g["create_transfer"](*changed)

    def test_source_contains_no_execution_or_provider_branch(self):
        source = Path(__file__).parents[3] / "shared/py/langboard_shared/domain/services/AppTransfers.py"
        text = source.read_text()
        for forbidden in ("execution_generations", "advance_generation", "AppExecutionRequest(", "start_execution", "httpx", "requests.post"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
