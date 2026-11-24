"""Exercise readiness against disposable HTTP servers; no live instance is used."""

import http.server
import runpy
import threading
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "status,runtime,version,ready",
    [
        (204, "phoenix", "fixture", True),
        (204, "other", "fixture", False),
        (204, "phoenix", "", False),
        (401, "phoenix", "fixture", False),
        (302, "phoenix", "fixture", False),
        (503, "phoenix", "fixture", False),
    ],
)
def test_requires_ready_runtime_and_version_on_both_consumer_routes(
    monkeypatch, capsys, status, runtime, version, ready
):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            requests.append((self.path, dict(self.headers)))
            self.send_response(status)
            self.send_header("x-langboard-socket-runtime", runtime)
            self.send_header("x-langboard-socket-version", version)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *_args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = f"http://127.0.0.1:{server.server_port}"
            monkeypatch.setenv("SOCKET_INTERNAL_URL", address + "/runtime")
            monkeypatch.setenv("SOCKET_EDITOR_INTERNAL_URL", address + "/editor")
            monkeypatch.setattr("time.sleep", lambda _seconds: None)
            probe = runpy.run_path(str(ROOT / "scripts/check-external-phoenix-ingress.py"))["check_ingresses"]
            if ready:
                probe()
                assert [path for path, _headers in requests] == ["/runtime/health/ready", "/editor/health/ready"]
                assert "SOCKET_EDITOR_INTERNAL_URL" in capsys.readouterr().out
            else:
                with pytest.raises(RuntimeError, match="SOCKET_INTERNAL_URL: no compatible"):
                    probe()
                assert len(requests) == 20
                assert {path for path, _headers in requests} == {"/runtime/health/ready"}
            assert all("Authorization" not in headers for _path, headers in requests)
        finally:
            server.shutdown()
            thread.join(timeout=2)
