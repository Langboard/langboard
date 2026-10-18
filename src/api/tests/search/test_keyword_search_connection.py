"""Real official-client HTTPS requests; this fixture is not an Elasticsearch cluster."""

import importlib
import json
import os
import ssl
import subprocess
import sys
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import sleep
import pytest
from langboard.commands.CheckKeywordSearchCommand import (
    CheckKeywordSearchCommand,
    CheckKeywordSearchCommandOptions,
    check_elasticsearch_connection,
)
from langboard_shared.core.bootstrap import Commander
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.search.ElasticsearchKeywordSearch import search_document_candidates
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAttachment,
    CardComment,
    EmployeeMembershipPolicy,
    ProjectAssignedUser,
    ProjectColumn,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import delete, update


INDEX = "langboard-fixture-keywords-v1"
SECRET = "fixture-key-never-print"


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keyword-tls")
    certificate, key = directory / "ca.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


@pytest.fixture
def endpoint(certificate):
    pytest.importorskip("elasticsearch", reason="Install the optional keyword-search extra for HTTPS client tests")
    cert, key = certificate
    calls = []
    state = {"mode": "ok", "monitor": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, json.loads(body) if body else {}, self.headers.get("Authorization")))
            mode = state["mode"]
            status = int(mode) if mode in {"401", "403", "404", "406", "503"} else 200
            if mode == "missing-index" and self.path.startswith("/_resolve/index/"):
                status = 404
            if mode == "timeout":
                sleep(1.2)
            if status != 200:
                value = {"error": {"type": "fixture", "reason": SECRET}, "status": status}
            elif self.path.startswith("/_security/user/_has_privileges"):
                privileges = ["read", "view_index_metadata", "index", "delete"]
                value = {
                    "cluster": {"monitor": state["monitor"]},
                    "index": {
                        INDEX: {name: mode != "missing-write" or name not in {"index", "delete"} for name in privileges}
                    },
                }
                if mode == "malformed":
                    value = {"index": []}
            elif self.path.startswith("/_resolve/index/"):
                value = {"indices": [{"name": INDEX}], "aliases": [], "data_streams": []}
                if mode == "alias":
                    value = {"indices": [], "aliases": [{"name": INDEX}], "data_streams": []}
            elif self.path == "/":
                value = {"version": {"number": "7.17.3" if mode == "legacy" else "9.5.1"}}
            else:
                value = {"timed_out": mode == "incomplete", "_shards": {"failed": 0}, "hits": {"private": SECRET}}
                if "candidate_hits" in state:
                    callback = state.get("before_search")
                    if callback:
                        callback()
                    value["hits"] = {"hits": state["candidate_hits"]}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if mode != "wrong-product":
                self.send_header("X-Elastic-Product", "Elasticsearch")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(value).encode())
            except (BrokenPipeError, ssl.SSLError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", str(cert), calls, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("monitor", [True, False])
def test_preflight_uses_read_only_protocol_without_requiring_cluster_monitor(endpoint, monitor):
    url, cert, calls, state = endpoint
    state["monitor"] = monitor
    result = check_elasticsearch_connection(url, INDEX, SECRET, ca_certs=cert, require_write=True)
    assert result["ok"] is True and result["data_mutated"] is False
    assert result["version_verified"] is monitor
    assert result["server_version"] == ("9.5.1" if monitor else None)
    assert result["write_permissions_verified"] is True
    assert all(
        method in {"GET", "POST"}
        and (
            path == "/" or path.startswith(("/_security/user/_has_privileges", "/_resolve/index/", f"/{INDEX}/_search"))
        )
        for method, path, _, _ in calls
    )
    search = next(body for _, path, body, _ in calls if path.startswith(f"/{INDEX}/_search"))
    assert search["size"] == 0 and search["query"] == {"match_none": {}}
    assert search["_source"] is False
    assert all(auth == "ApiKey " + SECRET for _, _, _, auth in calls)
    assert SECRET not in json.dumps(result)
    assert ("/" in [path for _, path, _, _ in calls]) is monitor


@pytest.mark.parametrize(
    "mode,code",
    [
        ("401", "authentication_failed"),
        ("403", "permission_denied"),
        ("404", "endpoint_unavailable"),
        ("missing-index", "index_unavailable"),
        ("406", "incompatible_server"),
        ("503", "server_request_failed"),
        ("legacy", "incompatible_server"),
        ("wrong-product", "incompatible_server"),
        ("alias", "concrete_index_required"),
        ("malformed", "invalid_server_response"),
        ("incomplete", "incomplete_search"),
        ("missing-write", "insufficient_index_privileges"),
        ("timeout", "connection_timeout"),
    ],
)
def test_errors_are_distinct_sanitized_and_do_not_retry(endpoint, mode, code):
    url, cert, calls, state = endpoint
    state["mode"] = mode
    result = check_elasticsearch_connection(url, INDEX, SECRET, ca_certs=cert, require_write=True, timeout=1)
    assert result["ok"] is False and result["code"] == code
    assert SECRET not in json.dumps(result)
    if mode in {"401", "403", "404", "406", "503", "timeout", "wrong-product"}:
        assert len(calls) == 1


@pytest.mark.parametrize("mode", ["untrusted", "hostname", "malformed-ca"])
def test_invalid_tls_is_rejected_before_sending_credentials(endpoint, mode, tmp_path):
    url, cert, calls, _ = endpoint
    ca_certs = ""
    if mode == "hostname":
        url = url.replace("localhost", "127.0.0.1")
        ca_certs = cert
    elif mode == "malformed-ca":
        invalid_ca = tmp_path / "invalid-ca.pem"
        invalid_ca.write_text("invalid CA")
        ca_certs = str(invalid_ca)
    result = check_elasticsearch_connection(url, INDEX, SECRET, ca_certs=ca_certs)
    assert result["code"] == "tls_verification_failed"
    assert calls == []


@pytest.mark.parametrize(
    "endpoint,index",
    [
        ("http://example.invalid", INDEX),
        ("https://user:password@example.invalid", INDEX),
        ("https://example.invalid?key=secret", INDEX),
        ("https://example.invalid", "*"),
        ("https://example.invalid", "other,index"),
        ("https://example.invalid", ".system"),
    ],
)
def test_unsafe_configuration_is_rejected_before_network(endpoint, index):
    assert check_elasticsearch_connection(endpoint, index, SECRET)["code"] == "invalid_configuration"


def test_cli_failure_uses_exit_status_and_does_not_echo_environment_secret(monkeypatch, capsys):
    monkeypatch.setenv("LANGBOARD_ELASTICSEARCH_API_KEY", SECRET)
    with pytest.raises(SystemExit) as error:
        CheckKeywordSearchCommand().execute(
            CheckKeywordSearchCommandOptions(endpoint="http://example.invalid", index=INDEX)
        )
    assert error.value.code == 1
    receipt = capsys.readouterr()
    assert SECRET not in receipt.out + receipt.err
    assert json.loads(receipt.out)["code"] == "invalid_configuration"


def test_native_commander_parses_the_connection_check_options(endpoint, monkeypatch, capsys):
    url, cert, _, _ = endpoint
    monkeypatch.setenv("LANGBOARD_ELASTICSEARCH_API_KEY", SECRET)
    monkeypatch.setattr(
        importlib.import_module("langboard_shared.core.bootstrap.Commander"),
        "argv",
        [
            "langboard",
            "search:check",
            "--endpoint",
            url,
            "--index",
            INDEX,
            "--ca-certs",
            cert,
            "--require-write",
            "--timeout",
            "1",
        ],
    )
    commander = Commander()
    commander.add_commands(CheckKeywordSearchCommand())
    commander.run()
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert json.loads(output.out)["write_permissions_verified"] is True


def test_default_import_and_missing_extra_are_independent_of_elasticsearch():
    env = {**os.environ, "PROJECT_NAME": "langboard"}
    script = """
import importlib.abc, sys
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"elasticsearch", "elastic_transport"}:
            raise ImportError("optional dependency absent")
sys.meta_path.insert(0, Absent())
from langboard.commands.CheckKeywordSearchCommand import check_elasticsearch_connection
assert "elasticsearch" not in sys.modules
from langboard_shared.core.search.ElasticsearchKeywordSearch import search_document_candidates
assert search_document_candidates("fixture", "query") is None
assert "elasticsearch" not in sys.modules
result = check_elasticsearch_connection("https://example.invalid", "instance-keywords", "fixture")
assert result["code"] == "keyword_search_extra_required", result
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True, text=True, timeout=10)


def configure_document_search(monkeypatch, endpoint):
    url, cert, _, _ = endpoint
    for name, value in {
        "DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED": "true",
        "DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT": url,
        "DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX": INDEX,
        "DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS": cert,
        "LANGBOARD_ELASTICSEARCH_API_KEY": SECRET,
    }.items():
        monkeypatch.setenv(name, value)


def test_candidate_protocol_is_bounded_and_does_not_trust_remote_text(endpoint, monkeypatch):
    configure_document_search(monkeypatch, endpoint)
    _, _, calls, state = endpoint
    board, card, attachment = (SnowflakeID(value).to_short_code() for value in (10, 101, 201))
    source = {"board_uid": board, "card_uid": card, "attachment_uid": attachment, "projection_hash": "a" * 64}
    state["candidate_hits"] = [
        {"_source": {**source, "text": SECRET}},
        {"_source": {**source, "board_uid": "foreign"}},
        {"_source": {**source, "projection_hash": "invalid"}},
    ]
    assert search_document_candidates(board, "billing") == [
        {key: source[key] for key in ("card_uid", "attachment_uid", "projection_hash")}
    ]
    method, path, body, _ = calls[0]
    assert method == "POST" and path.startswith(f"/{INDEX}/_search")
    assert body["size"] == 20 and "text" not in body["_source"]
    assert body["query"]["bool"]["filter"] == [{"term": {"board_uid": board}}]
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED", "false")
    assert search_document_candidates(board, "billing") is None
    assert len(calls) == 1


@pytest.fixture
def document_board(board, monkeypatch):
    _, actor, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardAttachment, CardComment, EmployeeMembershipPolicy):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: ""))
    sources = {}
    hits = []
    with DbSession.use(readonly=False) as db:
        removed_column = ProjectColumn(project_id=project.id, name="Removed workflow", deleted_at=SafeDateTime.now())
        foreign_column = ProjectColumn(project_id=11, name="Foreign workflow")
        db.insert(removed_column)
        db.insert(foreign_column)
        for title, values in (
            ("Visible", {}),
            ("Internal", {"visibility": "INTERNAL"}),
            ("Private", {"visibility": "PRIVATE", "owner_user_id": 2, "created_by_user_id": 2}),
            ("Own private", {"visibility": "PRIVATE", "owner_user_id": actor.id, "created_by_user_id": actor.id}),
            ("Foreign", {"project_id": 11}),
            ("Deleted", {"deleted_at": SafeDateTime.now()}),
            ("Stale", {}),
            ("Deleted attachment", {}),
            ("Oversized", {}),
            ("Wrong link", {}),
            ("Removed workflow", {"project_column_id": removed_column.id}),
            ("Foreign workflow", {"project_column_id": foreign_column.id}),
        ):
            card = Card(project_id=project.id, project_column_id=columns[0].id, title=title, visibility="SHARED")
            for key, value in values.items():
                setattr(card, key, value)
            db.insert(card)
            text = "Current invoice source" if title != "Oversized" else "x" * 131073
            attachment = CardAttachment(
                card_id=card.id,
                user_id=actor.id,
                filename="invoice.pdf",
                document_text=text,
                file=FileModel(
                    storage_type="test",
                    storage_name="test",
                    original_filename="invoice.pdf",
                    filename="invoice.pdf",
                    path="/test/invoice.pdf",
                ),
                deleted_at=SafeDateTime.now() if title == "Deleted attachment" else None,
            )
            db.insert(attachment)
            sources[title] = (card, attachment)
            hits.append(
                {
                    "_source": {
                        "board_uid": project.get_uid(),
                        "card_uid": sources["Visible"][0].get_uid() if title == "Wrong link" else card.get_uid(),
                        "attachment_uid": attachment.get_uid(),
                        "projection_hash": "a" * 64 if title == "Stale" else sha256(text.encode()).hexdigest(),
                        "text": SECRET,
                    }
                }
            )
        db.insert(
            Card(
                project_id=project.id,
                project_column_id=columns[0].id,
                title="billing from PostgreSQL",
                visibility="SHARED",
            )
        )
    service = DomainService()
    try:
        yield service, actor, project, sources, hits
    finally:
        service.close()


@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Mcp, CollaborationChannel.Api])
def test_real_https_candidates_merge_with_primary_search_without_exposing_hidden_or_stale_sources(
    document_board, endpoint, monkeypatch, channel
):
    service, actor, project, sources, hits = document_board
    configure_document_search(monkeypatch, endpoint)
    _, _, _, state = endpoint
    state["candidate_hits"] = hits
    results = service.card.search_context_by_project(project, "billing", user=actor, channel=channel)
    expected = {"Visible", "billing from PostgreSQL"}
    if channel != CollaborationChannel.Api:
        expected.add("Own private")
    assert {card["title"] for card in results} == expected
    visible = next(card for card in results if card["title"] == "Visible")
    assert visible["document_matches"] == [
        {
            "attachment_uid": sources["Visible"][1].get_uid(),
            "filename": "invoice.pdf",
            "snippet": "Current invoice source",
        }
    ]
    assert SECRET not in json.dumps(results)
    native = next(card for card in results if card["title"] == "billing from PostgreSQL")
    assert "document_matches" not in native
    state["mode"] = "503"
    results = service.card.search_context_by_project(project, "billing", user=actor, channel=channel)
    assert [card["title"] for card in results] == ["billing from PostgreSQL"]


@pytest.mark.parametrize("change", ["membership", "source"])
def test_primary_authority_and_hash_are_rechecked_after_remote_request(document_board, endpoint, monkeypatch, change):
    service, actor, project, sources, hits = document_board
    configure_document_search(monkeypatch, endpoint)
    _, _, _, state = endpoint
    state["candidate_hits"] = hits[:1]

    def change_during_request():
        with DbSession.use(readonly=False) as db:
            if change == "membership":
                db.exec(
                    delete(ProjectAssignedUser).where(
                        ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id
                    )
                )
            else:
                db.exec(
                    update(CardAttachment)
                    .where(CardAttachment.id == sources["Visible"][1].id)
                    .values(document_text="Replaced source")
                )

    state["before_search"] = change_during_request
    results = service.card.search_context_by_project(
        project, "billing", user=actor, channel=CollaborationChannel.HumanUI
    )
    assert [card["title"] for card in results] == ([] if change == "membership" else ["billing from PostgreSQL"])
