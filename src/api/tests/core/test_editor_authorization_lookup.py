"""Execute production authorization functions with controlled service boundaries.

Run without application dependencies: python3 -m unittest discover -s src/api/tests/core
-p test_editor_authorization_lookup.py. Set EDITOR_AUTH_BASELINE=HEAD to replay the
same checks against a pre-change git revision. No database or HTTP proof implied.
"""
import ast
from enum import StrEnum
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[4]
AUTH = "src/api/langboard/routes/auth/SocketAuthorization.py"
ROUTES = "src/api/langboard/routes/auth/SocketAuthApi.py"


def source(path):
    revision = os.getenv("EDITOR_AUTH_BASELINE")
    if revision:
        return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


def load_functions(path, namespace):
    tree = ast.parse(source(path))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    for node in functions:
        node.decorator_list = []
        node.args.defaults = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)


class AuthorizationLookupTest(unittest.TestCase):
    def setUp(self):
        # Keep actual function bodies, including parser, read/write policy and callers.
        attrs = {}
        for node in ast.walk(ast.parse(source(AUTH))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                attrs.setdefault(node.value.id, set()).add(node.attr)
        self.ns = {name: NS(**{attr: attr for attr in values}) for name, values in attrs.items()}
        self.ns["EEditorCollaborationType"] = StrEnum("EditorType", {"Card": "card", "Wiki": "wiki", "BoardColumnName": "board-column-name", "BoardSettings": "board-settings", "BotSchedule": "bot-schedule", "AppSettings": "app-settings"})
        self.ns["ProjectRoleAction"] = StrEnum("ProjectAction", {name: name for name in attrs["ProjectRoleAction"]})
        self.ns.update(_EDITOR_SETTINGS_TOPICS={}, _CARD_COMMENT_SECTION_PREFIX="comment-", ALL_GRANTED="all")
        load_functions(AUTH, self.ns)
        load_functions(ROUTES, self.ns)
        self.calls = []
        self.user = NS(id=7, is_admin=False, email="user", get_fullname=lambda: "User")
        self.card = NS(id=3, project_id=5, get_uid=lambda: "card1")
        self.project = NS(id=5)
        self.visible = self.assigned = self.can_update = self.can_mutate = True
        self.generation = self.admitted = True
        self.comment = NS(card_id=3, deleted_at=None)
        def lookup(kind, value):
            self.calls.append(kind)
            return value
        self.service = NS(
            card=NS(get_by_id_like=lambda _: lookup("card", self.card)),
            project=NS(get_by_id_like=lambda _: lookup("project", self.project),
                       is_assigned=lambda *_: (lookup("assigned", self.assigned),),
                       get_user_role_actions_by_project=lambda *_: ["CardUpdate"] if self.can_update else []),
            card_comment=NS(get_by_id_like=lambda _: self.comment, can_mutate=lambda *_: self.can_mutate),
            project_wiki=NS(get_by_id_like=lambda _: NS(project_id=5), is_assigned=lambda *_: True),
        )
        def generation(name, *, admission=False):
            self.calls.append("admission" if admission else "generation")
            return name.count(":") == 3 and self.generation and (not admission or self.admitted)
        self.ns.update(
            can_receive_card_event=lambda *_: lookup("audience", self.visible),
            editor_document_generation_is_current=generation,
            _authenticate_socket_user=lambda _: lookup("authenticate", self.user),
            _authenticate_editor_http_user=lambda _: lookup("authenticate", self.user),
            JsonResponse=lambda value: value,
        )

    def call(self, name="card:card1:title", admission=False):
        return self.ns["authorize_socket_editor_document"](
            NS(headers={"X-Socket-Editor-Admission": "registered-v1"} if admission else {}),
            NS(document_name=name), self.service)

    def test_card_one_read_write_lookup(self):
        self.assertEqual(self.call(), {"authorized": True, "writable": True, "user_name": "User"})
        for boundary in ("audience", "card", "project", "assigned"):
            self.assertEqual(self.calls.count(boundary), 1, (boundary, self.calls))
        self.assertEqual(self.calls[0], "authenticate")

    def test_private_card_denies_admin_too(self):
        self.user.is_admin = True
        self.visible = False
        self.assertEqual(self.call(), {"authorized": False, "writable": False, "user_name": None})
        self.assertNotIn("card", self.calls)

    def test_comment_author_overrides_role_and_admin(self):
        self.can_update = False
        self.assertTrue(self.call("card:card1:comment-comment1")["writable"])
        self.can_mutate = False
        self.user.is_admin = True
        self.assertFalse(self.call("card:card1:comment-comment1")["writable"])
        self.comment.card_id = 99
        self.can_mutate = True
        self.assertFalse(self.call("card:card1:comment-comment1")["writable"])

    def test_read_only_and_unassigned(self):
        self.can_update = False
        self.assertEqual((self.call()["authorized"], self.call()["writable"]), (True, False))
        self.assigned = False
        self.assertFalse(self.call()["authorized"])
        self.card = None
        self.assertFalse(self.call()["authorized"])

    def test_wiki_generation_and_registered_admission(self):
        result = self.call("wiki:wiki1:content:4", admission=True)
        self.assertTrue(result["authorized"])
        self.assertTrue(result["admission_ordered"])
        self.assertLess(self.calls.index("generation"), self.calls.index("admission"))
        self.admitted = False
        self.assertFalse(self.call("wiki:wiki1:content:4", admission=True)["authorized"])
        self.admitted = True
        self.generation = False
        self.assertFalse(self.call("wiki:wiki1:content:4", admission=True)["authorized"])
        self.assertFalse(self.call("wiki:wiki1:content", admission=True)["authorized"])
        self.assertFalse(self.call(admission=True)["authorized"])

    def test_http_write_and_batch_share_lookup(self):
        for route, form in (
            ("authorize_socket_editor_http_document", NS(document_name="card:card1:title", write=True)),
            ("authorize_socket_editor_http_documents", NS(document_names=["card:card1:title"] * 2, write=True)),
        ):
            self.calls.clear()
            self.assertEqual(self.ns[route](NS(headers={}), form, self.service), {"authorized": True})
            self.assertEqual(self.calls.count("card"), 1)
            self.assertEqual(self.calls.count("audience"), 1)

    def test_http_read_does_not_check_comment_mutation(self):
        def unexpected(*_):
            raise AssertionError("Read-only requests must not resolve write permissions")
        self.service.card_comment.get_by_id_like = unexpected
        result = self.ns["authorize_socket_editor_http_document"](
            NS(headers={}), NS(document_name="card:card1:comment-comment1", write=False), self.service)
        self.assertEqual(result, {"authorized": True})

    def test_invalid_name_denied(self):
        for name in ("", "card::title", "wiki:w:content:01", "unknown:x"):
            self.assertFalse(self.call(name)["authorized"])


if __name__ == "__main__":
    unittest.main()
