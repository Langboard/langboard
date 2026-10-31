"""Real official-client HTTPS requests; this fixture is not an Elasticsearch cluster."""

import importlib
import importlib.util
import json
import os
import ssl
import subprocess
import sys
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.commands.CheckKeywordSearchCommand import (
    CheckKeywordSearchCommand,
    CheckKeywordSearchCommandOptions,
    check_elasticsearch_connection,
)
from langboard_shared.core.bootstrap import Commander
from langboard_shared.core.db import DbSession, SqlBuilder
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
    CardCommentCompaction,
    CardDocumentArtifact,
    CardMetadata,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentKeywordIndex,
    EmployeeMembershipPolicy,
    InternalBot,
    KeywordSearchConfiguration,
    KeywordSearchConnection,
    ProjectAssignedUser,
    ProjectColumn,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langboard_shared.tasks.docling import DocumentKeywordTask as keyword
from sqlalchemy import delete, inspect, text, update
from sqlalchemy.exc import IntegrityError


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

        def do_PUT(self):
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
            elif self.path.startswith(f"/{INDEX}/_doc/"):
                identifier = urlsplit(self.path).path.rsplit("/", 1)[-1]
                stored = state.setdefault("keyword_documents", {})
                previous = stored.get(identifier)
                if self.command == "PUT":
                    version = int(parse_qs(urlsplit(self.path).query)["version"][0])
                    if previous and previous[0] >= version:
                        status = 409
                        value = {
                            "error": {"type": "version_conflict_engine_exception", "reason": SECRET},
                            "status": 409,
                        }
                    else:
                        stored[identifier] = (version, json.loads(body))
                        value = {"_id": identifier, "_version": version, "result": "created"}
                        callback = state.pop("after_write", None)
                        if callback:
                            callback()
                        if state.get("delay_write"):
                            sleep(1.2)
                else:
                    value = {
                        "_version": previous[0],
                        "_source": {key: value for key, value in previous[1].items() if key != "text"},
                    }
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
    env = {**os.environ, "PROJECT_NAME": "langboard", "DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED": "false"}
    script = """
import importlib.abc, sys
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"elasticsearch", "elastic_transport"}:
            raise ImportError("optional dependency absent")
        if fullname in {"langboard_shared.domain.services", "langboard_shared.core.db"}:
            raise AssertionError("Missing optional client must not initialize database services")
sys.meta_path.insert(0, Absent())
from langboard.commands.CheckKeywordSearchCommand import check_elasticsearch_connection
assert "elasticsearch" not in sys.modules
from langboard_shared.core.search.ElasticsearchKeywordSearch import (
    search_document_candidates, search_document_candidates_many,
)
assert search_document_candidates("fixture", "query") is None
assert search_document_candidates_many(["fixture"], "query") is None
assert "elasticsearch" not in sys.modules
result = check_elasticsearch_connection("https://example.invalid", "instance-keywords", "fixture")
assert result["code"] == "keyword_search_extra_required", result
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True, text=True, timeout=10)


def test_installed_client_stays_unimported_when_native_and_legacy_reads_are_disabled():
    pytest.importorskip("elasticsearch")
    env = {
        **os.environ,
        "PROJECT_NAME": "langboard",
        "MAIN_DATABASE_URL": "sqlite://",
        "DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED": "false",
    }
    script = """
import sys
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import KeywordSearchConfiguration
KeywordSearchConfiguration.__table__.create(DbEngine.get_main_engine())
from langboard_shared.core.search.ElasticsearchKeywordSearch import (
    search_document_candidates, search_document_candidates_many,
)
assert "elasticsearch" not in sys.modules
assert search_document_candidates("fixture", "query") is None
assert search_document_candidates_many(["fixture"], "query") is None
assert "elasticsearch" not in sys.modules
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


def test_candidate_protocol_is_bounded_and_does_not_trust_remote_text(document_board, endpoint, monkeypatch):
    configure_document_search(monkeypatch, endpoint)
    _, _, calls, state = endpoint
    board, card, attachment = (SnowflakeID(value).to_short_code() for value in (10, 101, 201))
    source = {
        "active": True,
        "board_uid": board,
        "card_uid": card,
        "attachment_uid": attachment,
        "projection_hash": "a" * 64,
    }
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
    assert body["query"]["bool"]["filter"] == [{"term": {"board_uid": board}}, {"term": {"active": True}}]
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED", "false")
    assert search_document_candidates(board, "billing") is None
    assert len(calls) == 1


def test_native_read_configuration_overrides_disabled_legacy_flag(document_board, secrets, endpoint, monkeypatch):
    from types import SimpleNamespace
    from langboard_shared.domain.services import KeywordSearchSettings as settings
    from pydantic import SecretStr

    secret_service, board, _ = secrets
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    reference = secret_service.create(actor, "instance", "instance", "keyword/native-read", SecretStr(SECRET))
    url, cert, calls, _ = endpoint
    settings.save(
        actor,
        SimpleNamespace(secret_reference=secret_service),
        expected_revision=0,
        endpoint=url,
        index=INDEX,
        secret_ref=reference["uri"],
        secret_revision=0,
        reads_enabled=True,
        indexing_enabled=False,
    )
    monkeypatch.setattr(DomainService, "secret_reference", property(lambda _: secret_service))
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED", "false")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS", str(cert))
    assert search_document_candidates("fixture", "query") == []
    assert len(calls) == 1 and calls[0][1].startswith(f"/{INDEX}/_search")


@pytest.fixture
def document_board(board, monkeypatch):
    _, actor, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (
        Bot,
        Card,
        CardAttachment,
        CardComment,
        InternalBot,
        CardCommentCompaction,
        CardMetadata,
        CardDocumentArtifact,
        EmployeeMembershipPolicy,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentKeywordIndex,
        KeywordSearchConfiguration,
        KeywordSearchConnection,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: ""))
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "false")
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
                        "active": True,
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


def test_deleted_extraction_clears_legacy_projection_and_rejects_old_remote_candidates(
    document_board, endpoint, monkeypatch
):
    service, actor, project, sources, hits = document_board
    configure_document_search(monkeypatch, endpoint)
    _, _, _, state = endpoint
    state["candidate_hits"] = hits[:1]
    card, attachment = sources["Visible"]
    # The source has no metadata row: clearing a leftover projection must still work.
    assert service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid()) is None
    service.docling_metadata.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    results = service.card.search_context_by_project(
        project, "billing", user=actor, channel=CollaborationChannel.HumanUI
    )
    assert [card["title"] for card in results] == ["billing from PostgreSQL"]
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id)).first()
        assert current.document_text == "" and current.file == attachment.file and current.deleted_at is None


@pytest.fixture
def keyword_source(document_board, endpoint, monkeypatch):
    configure_document_search(monkeypatch, endpoint)
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "true")
    send = Mock()
    monkeypatch.setattr(keyword.Broker.celery, "send_task", send)
    service, _, _, sources, _ = document_board
    card, attachment = sources["Visible"]
    assert service.docling_metadata.queue_document(
        CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={}
    )
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    return service, card, attachment, document, send


def keyword_job(uid):
    from langboard_shared.helpers import InfraHelper

    with DbSession.use(readonly=False) as db:
        return db.exec(
            SqlBuilder.select.table(DocumentKeywordIndex).where(DocumentKeywordIndex.id == InfraHelper.convert_id(uid))
        ).first()


def activate_keyword_source(source, text="Current derived text"):
    service, card, attachment, document, _ = source
    assert service.docling_metadata.mark_document_indexed(
        CardMetadata, card, attachment.get_uid(), "pdf", content_hash=sha256(b"fixture-original-pdf").hexdigest(),
        content={"markdown": text}, generation=document["generation"]
    )
    return keyword_job(document["keyword_index_job_uid"])


def test_keyword_projection_requires_hashed_current_generation(document_board, monkeypatch):
    service, _, _, sources, _ = document_board
    card, attachment = sources["Visible"]
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "true")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", "https://keyword.example.invalid")
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX", INDEX)
    monkeypatch.setenv("LANGBOARD_ELASTICSEARCH_API_KEY", SECRET)
    send = Mock()
    monkeypatch.setattr(keyword.Broker.celery, "send_task", send)
    assert service.docling_metadata.queue_document(
        CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={}
    )
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    job = keyword_job(document["keyword_index_job_uid"])
    assert keyword._payload(job) is None
    assert service.docling_metadata.mark_document_indexed(
        CardMetadata, card, attachment.get_uid(), "pdf", content={"markdown": "Unhashed source"},
        generation=document["generation"],
    )
    job = keyword_job(job.get_uid())
    assert keyword._payload(job) is None
    job = activate_keyword_source((service, card, attachment, document, send))
    payload = keyword._payload(job)
    assert payload["active"] is True and payload["text"] == "Current derived text"
    assert payload["projection_hash"] == sha256(b"Current derived text").hexdigest()
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "false")
    assert service.docling_metadata.queue_document(
        CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={}, force=True
    )
    # A pending request does not supersede the last successfully published text.
    assert keyword._payload(job) == payload
    requested = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert requested["generation"] != document["generation"]
    assert service.docling_metadata.mark_document_indexed(
        CardMetadata, card, attachment.get_uid(), "pdf", content_hash=sha256(b"fixture-original-pdf").hexdigest(),
        content={"markdown": "New derived text"}, generation=requested["generation"],
    )
    # Once the new text is published the old generation cannot be sent.
    assert keyword._payload(job) is None
    service.docling_metadata.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    removed = keyword_job(job.get_uid())
    assert removed.operation == "remove" and removed.version > job.version
    tombstone = keyword._payload(removed)
    assert tombstone["active"] is False and "text" not in tombstone


def test_keyword_snapshot_waits_for_transcription_and_dispatches_after_commit(keyword_source, endpoint, monkeypatch):
    _, _, attachment, document, send = keyword_source
    job = keyword_job(document["keyword_index_job_uid"])
    assert job.due_at is None and job.completed_version == 0
    assert endpoint[0] not in json.dumps(document) and SECRET not in json.dumps(document)
    send.assert_not_called()
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "false")
    with DbSession.atomic():
        ready = activate_keyword_source(keyword_source)
        assert ready.due_at is not None
        send.assert_not_called()
    send.assert_called_once_with(keyword.TASK_NAME, args=[int(job.id)])
    assert keyword.process_keyword(int(job.id))
    completed = keyword_job(job.get_uid())
    assert completed.completed_version == completed.version and completed.due_at is None
    stored = endpoint[3]["keyword_documents"][attachment.get_uid()]
    assert stored[0] == completed.version and stored[1]["text"] == "Current derived text"
    assert keyword.recover_keyword() == 0


def test_keyword_outcome_unknown_retry_checks_exact_external_version(keyword_source, endpoint, monkeypatch):
    _, _, attachment, _, send = keyword_source
    send.side_effect = RuntimeError(SECRET)
    job = activate_keyword_source(keyword_source)
    assert job.due_at is not None and job.completed_version == 0
    send.side_effect = None
    assert keyword.recover_keyword() == 1
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_WRITE_TIMEOUT", "1")
    endpoint[3]["delay_write"] = True
    assert keyword.process_keyword(int(job.id)) is False
    failed = keyword_job(job.get_uid())
    assert failed.last_error == "keyword_index_failed_check_original_connection"
    assert SECRET not in failed.model_dump_json()
    assert endpoint[3]["keyword_documents"][attachment.get_uid()][0] == job.version
    endpoint[3]["delay_write"] = False
    with DbSession.atomic() as db:
        db.exec(update(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job.id).values(due_at=SafeDateTime.now()))
    assert keyword.process_keyword(int(job.id))
    done = keyword_job(job.get_uid())
    assert done.completed_version == done.version and done.attempt_count == 2
    assert done.due_at is None and done.last_error is None
    puts = [path for method, path, _, _ in endpoint[2] if method == "PUT"]
    assert len(puts) == 2 and all("version_type=external" in path for path in puts)


def test_keyword_late_index_ack_cannot_override_delete_tombstone(keyword_source, endpoint):
    service, card, attachment, _, _ = keyword_source
    job = activate_keyword_source(keyword_source)
    payload = keyword._payload(job)
    endpoint[3]["after_write"] = lambda: service.docling_metadata.delete_document_by_attachment_uid(
        CardMetadata, card, attachment.get_uid()
    )
    assert keyword.process_keyword(int(job.id)) is False
    deletion = keyword_job(job.get_uid())
    assert deletion.operation == "remove" and deletion.version > job.version
    assert keyword.process_keyword(int(job.id))
    version, tombstone = endpoint[3]["keyword_documents"][attachment.get_uid()]
    assert version == deletion.version and tombstone["active"] is False and "text" not in tombstone
    with pytest.raises(ValueError, match="version conflict"):
        keyword._send(job, payload)
    assert endpoint[3]["keyword_documents"][attachment.get_uid()] == (version, tombstone)


def test_keyword_superseded_generation_stops_without_remote_work(keyword_source, endpoint, monkeypatch):
    service, card, attachment, _, _ = keyword_source
    job = activate_keyword_source(keyword_source)
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "false")
    assert service.docling_metadata.queue_document(
        CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={}, force=True
    )
    assert keyword._payload(job)["text"] == "Current derived text"
    requested = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert service.docling_metadata.mark_document_indexed(
        CardMetadata, card, attachment.get_uid(), "pdf", content_hash=sha256(b"fixture-original-pdf").hexdigest(),
        content={"markdown": "New derived text"}, generation=requested["generation"],
    )
    assert keyword.process_keyword(int(job.id)) is False
    stopped = keyword_job(job.get_uid())
    assert stopped.last_error == "source_superseded" and stopped.due_at is None
    assert endpoint[2] == []
    service.docling_metadata.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert keyword.process_keyword(int(job.id))
    assert endpoint[3]["keyword_documents"][attachment.get_uid()][1]["active"] is False


def test_keyword_connection_change_retains_original_target_without_sending_key(keyword_source, endpoint, monkeypatch):
    job = activate_keyword_source(keyword_source)
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT", "https://changed.invalid")
    assert keyword.process_keyword(int(job.id)) is False
    retained = keyword_job(job.get_uid())
    assert retained.destination == job.destination and retained.completed_version == 0
    assert retained.due_at is not None and endpoint[2] == []


def test_global_keyword_enable_does_not_authorize_an_existing_upload(document_board, endpoint, monkeypatch):
    service, _, _, sources, _ = document_board
    card, attachment = sources["Visible"]
    assert service.docling_metadata.queue_document(
        CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={}
    )
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert "keyword_index_job_uid" not in document
    configure_document_search(monkeypatch, endpoint)
    monkeypatch.setenv("DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED", "true")
    assert service.docling_metadata.mark_document_indexed(
        CardMetadata,
        card,
        attachment.get_uid(),
        "pdf",
        content={"markdown": "Existing upload"},
        generation=document["generation"],
    )
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentKeywordIndex)).all() == []
    assert endpoint[2] == []


def test_keyword_transaction_rollback_preserves_request_and_dispatch(keyword_source):
    _, card, attachment, document, send = keyword_source
    original = keyword_job(document["keyword_index_job_uid"])
    with pytest.raises(RuntimeError, match="abort"):
        with DbSession.atomic() as db:
            keyword.activate_index(db, card, attachment.get_uid(), document["generation"], original.get_uid())
            keyword.record_removal(db, card, attachment.get_uid())
            raise RuntimeError("abort")
    retained = keyword_job(original.get_uid())
    assert retained.version == original.version and retained.operation == "index"
    assert retained.due_at is None and retained.completed_version == 0
    send.assert_not_called()


def test_keyword_foreign_card_cannot_activate_or_remove_request(keyword_source, document_board):
    _, _, attachment, document, send = keyword_source
    _, _, _, sources, _ = document_board
    foreign = sources["Foreign"][0]
    original = keyword_job(document["keyword_index_job_uid"])
    with DbSession.atomic() as db:
        keyword.activate_index(db, foreign, attachment.get_uid(), document["generation"], original.get_uid())
        keyword.record_removal(db, foreign, attachment.get_uid())
    retained = keyword_job(original.get_uid())
    assert retained.version == original.version and retained.due_at is None
    send.assert_not_called()


@pytest.mark.parametrize("scope", ["attachment", "card", "project"])
def test_keyword_cleanup_survives_missing_source_metadata(keyword_source, document_board, endpoint, scope):
    service, card, attachment, _, _ = keyword_source
    job = activate_keyword_source(keyword_source)
    assert keyword.process_keyword(int(job.id))
    service.docling_metadata.save_documents(CardMetadata, card, [])
    if scope == "attachment":
        service.docling_metadata.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    else:
        _, _, project, _, _ = document_board
        service.docling_metadata.record_scope_embedding_cleanup(project, card if scope == "card" else None)
    removal = keyword_job(job.get_uid())
    assert removal.operation == "remove" and removal.version > job.version and removal.due_at is not None
    assert keyword.process_keyword(int(job.id))
    assert endpoint[3]["keyword_documents"][attachment.get_uid()][1]["active"] is False


def test_keyword_expired_lease_fences_late_worker_ack(keyword_source, endpoint):
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Independent database transactions require PostgreSQL")
    job = activate_keyword_source(keyword_source)
    entered, release = Event(), Event()
    result = []

    def hold_first_write():
        entered.set()
        assert release.wait(10), "Second worker did not release the first request"

    endpoint[3]["after_write"] = hold_first_write
    worker = Thread(target=lambda: result.append(keyword.process_keyword(int(job.id))))
    worker.start()
    try:
        assert entered.wait(5), "First worker did not publish its projection"
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job.id).values(due_at=SafeDateTime.now())
            )
        assert keyword.process_keyword(int(job.id))
        completed = keyword_job(job.get_uid())
        assert completed.completed_version == completed.version and completed.attempt_count == 2
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and result == [False]
    retained = keyword_job(job.get_uid())
    assert retained.completed_version == completed.version and retained.due_at is None


def test_keyword_migration_preserves_owned_rows_and_enforces_versions(board):
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261019010000-d95e63b0f721.py"
    spec = importlib.util.spec_from_file_location("keyword_index_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert inspect(connection).get_foreign_keys("document_keyword_index") == []
        job = DocumentKeywordIndex(
            attachment_id=101,
            card_id=102,
            project_id=103,
            destination_fingerprint="fixture",
            destination={"endpoint": "https://example.invalid", "index": INDEX},
            source_generation="fixture-generation",
        )
        row = {column.name: getattr(job, column.name) for column in job.__table__.columns}
        row.pop("connection_id")
        from sqlalchemy import MetaData, Table
        historical = Table("document_keyword_index", MetaData(), autoload_with=connection)
        connection.execute(historical.insert().values(**row))
        for values in ({"version": 0}, {"completed_version": 2}, {"operation": "unknown"}):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(update(DocumentKeywordIndex).values(**values))
        with pytest.raises(RuntimeError, match="ownership"):
            migration.downgrade()
        assert connection.execute(historical.select()).first() is not None
        connection.execute(delete(DocumentKeywordIndex))
        migration.downgrade()
        assert "document_keyword_index" not in inspect(connection).get_table_names()


def test_keyword_native_celery_process_delivers_committed_projection(keyword_source, endpoint, tmp_path):
    engine = DbEngine.get_main_engine()
    if engine.dialect.name != "postgresql":
        pytest.skip("Independent producer and worker require PostgreSQL")
    _, _, attachment, document, send = keyword_source
    job = keyword_job(document["keyword_index_job_uid"])
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    isolated_url = engine.url.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(
        hide_password=False
    )
    for name in ("queue", "processed", "control"):
        (tmp_path / name).mkdir()
    environment = {
        **os.environ,
        "MAIN_DATABASE_URL": isolated_url,
        "READONLY_DATABASE_URL": isolated_url,
        "CACHE_TYPE": "redis",
        "CACHE_URL": "redis://127.0.0.1:1/0",
        "BROKER_URL": "filesystem://",
        "KEYWORD_TEST_BROKER_DIR": str(tmp_path),
        "ENVIRONMENT": "development",
        "IS_EXECUTABLE": "false",
        "SENTRY_DSN": "",
        "PYTHONPATH": os.pathsep.join(str(Path(path).resolve()) for path in sys.path if path),
    }
    command = [sys.executable, str(Path(__file__).resolve())]
    producer = subprocess.run(
        [*command, "keyword-produce", str(job.id)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert producer.returncode == 0, producer.stdout + producer.stderr
    send.assert_not_called()
    assert list((tmp_path / "queue").glob("*.msg")), "Native after-commit publication did not reach the broker"
    log_path = tmp_path / "keyword-worker.log"
    with log_path.open("w") as output:
        worker = subprocess.Popen(
            [*command, "keyword-worker"],
            cwd=tmp_path,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = monotonic() + 20
            while True:
                retained = keyword_job(job.get_uid())
                if retained.completed_version == retained.version and retained.completed_version > 0:
                    break
                assert worker.poll() is None and monotonic() < deadline, log_path.read_text()
                sleep(0.1)
            version, projection = endpoint[3]["keyword_documents"][attachment.get_uid()]
            assert version == retained.version and projection["text"] == "Native broker transcription"
            assert projection["active"] is True and retained.due_at is None
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
    assert SECRET not in log_path.read_text()


if __name__ == "__main__":
    directory = Path(os.environ["KEYWORD_TEST_BROKER_DIR"])
    keyword.Broker.celery.conf.update(
        task_default_queue="keyword-fixture",
        task_publish_retry=False,
        broker_transport_options={
            "data_folder_in": str(directory / "queue"),
            "data_folder_out": str(directory / "queue"),
            "processed_folder": str(directory / "processed"),
            "control_folder": str(directory / "control"),
        },
    )
    if sys.argv[1] == "keyword-produce":
        job = keyword_job(SnowflakeID(int(sys.argv[2])).to_short_code())
        with DbSession.use(readonly=False) as db:
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == job.card_id)).first()
        service = DomainService()
        try:
            assert service.docling_metadata.mark_document_indexed(
                CardMetadata,
                card,
                job.attachment_id.to_short_code(),
                "pdf",
                content_hash=sha256(b"fixture-original-pdf").hexdigest(),
                content={"markdown": "Native broker transcription"},
                generation=job.source_generation,
            )
        finally:
            service.close()
    elif sys.argv[1] == "keyword-worker":
        assert keyword.TASK_NAME in keyword.Broker.celery.tasks
        keyword.Broker.celery.worker_main(
            [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=info",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "-Q",
                "keyword-fixture",
                "--hostname=keyword-fixture@localhost",
            ]
        )


def test_native_upload_manual_worker_and_original_cleanup_share_grant(document_board, secrets, monkeypatch):
    """Real source/queue/vault flow with a controlled remote-send boundary."""
    from types import SimpleNamespace
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services import KeywordSearchSettings as settings
    from pydantic import SecretStr

    secret_service, board, _ = secrets
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    reference = secret_service.create(actor, "instance", "instance", "keyword/native", SecretStr("original-key"))
    host = SimpleNamespace(secret_reference=secret_service)
    first = settings.save(actor, host, expected_revision=0, endpoint="https://first.example.invalid", index=INDEX,
        secret_ref=reference["uri"], secret_revision=0, reads_enabled=False, indexing_enabled=True)
    monkeypatch.setattr(DomainService, "secret_reference", property(lambda _: secret_service))
    monkeypatch.setattr(keyword.Broker.celery, "send_task", Mock())
    from langboard_shared.core.security import KeyVault
    original_get = KeyVault.provider.get_key
    vault_reads = []
    def get_at_use(locator):
        assert not DbSession.has_active_transaction()
        vault_reads.append(locator)
        return original_get(locator)
    monkeypatch.setattr(KeyVault.provider, "get_key", get_at_use)
    service, _, _, sources, _ = document_board
    card, attachment = sources["Visible"]
    assert service.docling_metadata.queue_document(CardMetadata, card, attachment.get_uid(), attachment.filename, vision_config={})
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
    assert service.docling_metadata.mark_document_indexed(CardMetadata, card, attachment.get_uid(), "pdf", content_hash="fixture-hash", content={"markdown": "Current derived text"}, generation=document["generation"])
    job = keyword_job(document["keyword_index_job_uid"])
    assert job.connection_id is not None and vault_reads == []
    assert service.card_attachment.request_document_keyword_index(card.project_id, card, attachment, user=actor) == "pending"
    assert vault_reads == []
    requests = []
    def controlled_send(job, payload):
        config = settings.configuration(operation=job.operation, connection_id=job.connection_id)
        options = settings.runtime_options(config, operation=job.operation)
        requests.append((config["endpoint"], options["api_key"], payload["active"]))
    monkeypatch.setattr(keyword, "_send", controlled_send)
    assert keyword.process_keyword(int(job.id))
    assert requests == [("https://first.example.invalid", "original-key", True)]
    second_ref = secret_service.create(actor, "instance", "instance", "keyword/second", SecretStr("new-key"))
    settings.save(actor, host, expected_revision=first["revision"], endpoint="https://second.example.invalid", index=INDEX,
        secret_ref=second_ref["uri"], secret_revision=0, reads_enabled=False, indexing_enabled=True)
    with DbSession.atomic() as db:
        keyword.record_removal(db, card, attachment.get_uid())
    assert keyword.process_keyword(int(job.id))
    assert requests[-1] == ("https://first.example.invalid", "original-key", False)
