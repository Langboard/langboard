"""Real official-client HTTPS requests; this fixture is not an Elasticsearch cluster."""

import importlib
import json
import os
import ssl
import subprocess
import sys
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
result = check_elasticsearch_connection("https://example.invalid", "instance-keywords", "fixture")
assert result["code"] == "keyword_search_extra_required", result
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True, text=True, timeout=10)
