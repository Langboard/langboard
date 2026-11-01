"""The production provider factory uses encrypted FastMCP disk across processes."""

import getpass
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest


WORKER = Path(__file__).with_name("fixtures") / "native_oauth_disk_worker.py"


def test_native_oauth_default_disk_survives_process_restart(tmp_path):
    environment = {**os.environ, "FASTMCP_HOME": str(tmp_path)}

    def run(mode):
        result = subprocess.run(
            [sys.executable, str(WORKER), mode], env=environment, capture_output=True, text=True, timeout=30
        )
        # Output contains booleans only; never echo child logs that may contain tokens.
        assert result.returncode == 0, f"OAuth disk fixture failed in {mode} (exit {result.returncode})"
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload["mode"] == mode
        return payload

    first = run("write")
    assert run("read") == {**first, "mode": "read"}
    files = [path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    for marker in [b"isolated-upstream-access-marker", b"isolated-upstream-refresh-marker", b"isolated-subject"]:
        assert all(marker not in data for data in files)
    wrong = run("wrong-key")
    assert all(value is False for key, value in wrong.items() if key != "mode")
    assert run("read") == {**first, "mode": "read"}
    missing = run("missing-mapping")
    assert missing["client_restored"] and missing["upstream_restored"] and missing["refresh_metadata_restored"]
    assert not missing["access_validated"]


@pytest.fixture(scope="module")
def refresh_postgres():
    """Own a fresh PostgreSQL 17 cluster, including shutdown on failed assertions."""
    binaries = Path("/opt/homebrew/opt/postgresql@17/bin")
    if not (binaries / "initdb").is_file():
        pytest.skip("Disposable PostgreSQL 17 binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="native-oauth-pg-") as directory:
        root = Path(directory)
        data = root / "data"
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        subprocess.run(
            [str(binaries / "initdb"), "-D", str(data), "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True,
            capture_output=True,
        )
        started = False
        try:
            subprocess.run(
                [
                    str(binaries / "pg_ctl"),
                    "-D",
                    str(data),
                    "-l",
                    str(root / "server.log"),
                    "-o",
                    f"-h 127.0.0.1 -k {root} -p {port}",
                    "-w",
                    "start",
                ],
                check=True,
                capture_output=True,
            )
            started = True
            yield f"postgresql+psycopg://{getpass.getuser()}@127.0.0.1:{port}/postgres"
        finally:
            if started or (data / "postmaster.pid").exists():
                subprocess.run(
                    [str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                    check=True,
                    capture_output=True,
                )


@pytest.mark.parametrize("mode", ["baseline", "fenced"])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_process_refresh_overlap(tmp_path, refresh_postgres, mode, backend):
    """Hold the actual upstream until both workers overlap, before allowing rotation."""
    root = tmp_path
    database = refresh_postgres if backend == "postgresql" else f"sqlite:///{root / 'primary.sqlite'}"
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(True)
            assert release.wait(15)
            body = json.dumps(
                {
                    "access_token": "isolated-new-access-marker",
                    "refresh_token": "isolated-new-refresh-marker",
                    "scope": "openid",
                    "expires_in": 60,
                    "refresh_expires_in": 120,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/refresh"
    environment = {**os.environ, "FASTMCP_HOME": str(root / "encrypted")}
    worker = WORKER.with_name("native_oauth_refresh_worker.py")

    def command(operation, identity):
        return [sys.executable, str(worker), operation, database, str(root), endpoint, identity]

    def until(condition):
        deadline = time.monotonic() + 15
        while not condition():
            assert time.monotonic() < deadline, "Disposable workers did not reach the required overlap"
            time.sleep(0.01)

    children = []
    try:
        seed = subprocess.run(command("seed", "seed"), env=environment, capture_output=True, text=True, timeout=30)
        assert seed.returncode == 0, "Disposable encrypted storage seed failed"
        for identity in ("a", "b"):
            children.append(
                subprocess.Popen(
                    command(mode, identity), env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
            )
        until(lambda: len(list(root.glob("ready-*"))) == 2)
        (root / "go").touch()
        if mode == "baseline":
            until(lambda: len(calls) == 2)
        else:
            until(lambda: len(calls) == 1 and bool(list(root.glob("waiting-*"))))
        release.set()
        results = []
        for child in children:
            stdout, _ = child.communicate(timeout=20)
            assert child.returncode == 0, "Disposable encrypted refresh worker failed"
            results.append(json.loads(stdout.splitlines()[-1])["status"])
        assert len(calls) == (2 if mode == "baseline" else 1)
        assert sorted(results) == (["rotated", "rotated"] if mode == "baseline" else ["invalid_grant", "rotated"])
        files = [path.read_bytes() for path in (root / "encrypted").rglob("*") if path.is_file()]
        assert files
        assert all(b"isolated-refresh-marker" not in content for content in files)
        assert all(b"isolated-new-refresh-marker" not in content for content in files)
    finally:
        release.set()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
