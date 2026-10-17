"""External realtime selection must exclude the local runtime and ingress."""

import json
import shlex
import shutil
import subprocess
import pytest
from test_external_redis_compose import ROOT, selected_args


EXTERNAL = {
    "SOCKET_DEPLOYMENT_MODE": "external",
    "SOCKET_URL": "https://realtime.example.invalid",
    "SOCKET_INTERNAL_URL": "https://realtime-internal.example.invalid",
    "SOCKET_EDITOR_INTERNAL_URL": "https://editor-internal.example.invalid",
    "WITH_OTEL": "true",
}


@pytest.mark.parametrize("external", [False, True])
def test_compose_selects_one_realtime_runtime_and_ingress(tmp_path, external):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    selected = selected_args(tmp_path, EXTERNAL if external else {})
    assert selected.returncode == 0, selected.stderr
    args = shlex.split(selected.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            args[index] = str(ROOT / value)
    config = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            *args,
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert config.returncode == 0, config.stderr
    services = json.loads(config.stdout)["services"]
    assert ("socket-phoenix" in services) is not external
    server = services["server"]
    assert ("socket-phoenix" in server["depends_on"]) is not external
    assert (8010 in {port["target"] for port in server["ports"]}) is not external
    templates = {volume["target"]: volume["source"] for volume in server["volumes"]}
    socket_source = templates["/etc/nginx/templates/socket.conf.template"]
    assert socket_source.endswith("socket-disabled.conf.template" if external else "socket.conf.template")
    assert templates["/etc/nginx/templates/default.conf.template"].endswith("server/conf.template")
    assert {"api", "ui"}.issubset(server["depends_on"])
    assert {"api", "celeryworker", "graph", "kafka0", "kafka1", "kafka2", "redis", "redis-broker"}.issubset(services)
    if external:
        assert "socket-phoenix-otel-collector" not in services
        assert "--profile" not in args


@pytest.mark.parametrize("missing", ["SOCKET_URL", "SOCKET_INTERNAL_URL", "SOCKET_EDITOR_INTERNAL_URL"])
def test_external_mode_rejects_missing_endpoint(tmp_path, missing):
    selected = selected_args(tmp_path, {**EXTERNAL, missing: ""})
    assert selected.returncode != 0
    assert missing in selected.stderr


@pytest.mark.parametrize("value", ["ftp://invalid", "https://user:secret@invalid", "https://invalid?token=secret"])
def test_external_mode_rejects_incompatible_endpoint_without_echoing_it(tmp_path, value):
    selected = selected_args(tmp_path, {**EXTERNAL, "SOCKET_INTERNAL_URL": value})
    assert selected.returncode != 0
    assert "SOCKET_INTERNAL_URL" in selected.stderr
    assert value not in selected.stdout + selected.stderr


def test_unknown_mode_is_an_error(tmp_path):
    selected = selected_args(tmp_path, {"SOCKET_DEPLOYMENT_MODE": "unknown"})
    assert selected.returncode != 0
    assert "SOCKET_DEPLOYMENT_MODE" in selected.stderr


@pytest.mark.parametrize("url", ["wss://realtime.example.invalid", "ws://127.0.0.1:5690", "https://[::1]:5690/edge"])
def test_browser_endpoint_accepts_existing_transport_schemes(tmp_path, url):
    selected = selected_args(tmp_path, {**EXTERNAL, "SOCKET_URL": url})
    assert selected.returncode == 0, selected.stderr


def test_external_otel_preparation_does_not_pull_a_local_collector(tmp_path):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / ".env").write_text("SOCKET_DEPLOYMENT_MODE=external\n")
    result = subprocess.run(
        [
            shutil.which("make"),
            "prepare_socket_phoenix_otel",
            "WITH_OTEL=true",
            "COMPOSE_ARGS=-f ./docker/docker-compose.phoenix-external.yaml",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "SOCKET_DEPLOYMENT_MODE": "external"},
    )
    assert result.returncode == 0, result.stderr
    assert "docker" not in result.stderr


@pytest.mark.parametrize("valid", [True, False])
def test_powershell_selection_parity_when_available(tmp_path, valid):
    shell = shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is not installed")
    selected_args(tmp_path, EXTERNAL if valid else {**EXTERNAL, "SOCKET_INTERNAL_URL": ""})
    selected = subprocess.run(
        [shell, "-NoProfile", "-File", str(ROOT / "scripts/utils/get-compose-args.ps1"), "-WithOtel", "true"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (selected.returncode == 0) is valid
    if valid:
        assert ".phoenix-external.yaml" in selected.stdout
        assert ".otel.yaml" not in selected.stdout
    else:
        assert "SOCKET_INTERNAL_URL" in selected.stderr


def test_make_probes_external_from_api_and_preserves_failure(tmp_path):
    import os

    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / ".env").write_text("PROJECT_NAME=fixture\n")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/check-external-phoenix-ingress.py", scripts / "check-external-phoenix-ingress.py")
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > docker-argv\ncat > docker-stdin\nexit 23\n')
    docker.chmod(0o755)
    result = subprocess.run(
        [
            shutil.which("make"),
            "check_socket_phoenix_ingress",
            "COMPOSE_ARGS=-f ./docker/docker-compose.phoenix-external.yaml",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
    )
    assert result.returncode != 0
    assert "Error 23" in result.stderr
    assert (tmp_path / "docker-argv").read_text().splitlines()[:3] == ["exec", "-i", "fixture_api"]
    assert "check_ingresses" in (tmp_path / "docker-stdin").read_text()
    assert "curl" not in result.stderr
