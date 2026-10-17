"""Render the actual Compose selection without creating any service or volume."""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]
EXTERNAL = {
    "REDIS_EXTERNAL_CACHE_URL": "rediss://cache.invalid:6380/0",
    "REDIS_EXTERNAL_BROKER_URL": "rediss://queue.invalid:6380/0",
    "CACHE_KEY_PREFIX": "langboard:fixture:cache:",
    "BROKER_KEY_PREFIX": "langboard:fixture:queue:",
}


def selected_args(tmp_path, values):
    text = (ROOT / ".env.example").read_text()
    (tmp_path / ".env").write_text(text + "\nWITH_DB_BACKUP=false\n" + "\n".join(f"{k}={v}" for k, v in values.items()))
    return subprocess.run(
        [shutil.which("bash"), str(ROOT / "scripts/utils/get-compose-args.sh")],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_external_mode_renders_without_bundled_redis_dependencies(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    selected = selected_args(tmp_path, EXTERNAL)
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
        check=False,
    )
    assert config.returncode == 0, config.stderr
    services = json.loads(config.stdout)["services"]
    assert "redis" not in services
    assert "redis-broker" not in services
    for name in ("api", "celeryworker", "graph"):
        service = services[name]
        assert not {"redis", "redis-broker"}.intersection(service.get("depends_on", {}))
        assert service["environment"]["CACHE_URL"] == EXTERNAL["REDIS_EXTERNAL_CACHE_URL"]
        assert service["environment"]["BROKER_URL"] == EXTERNAL["REDIS_EXTERNAL_BROKER_URL"]
        assert service["environment"]["CACHE_KEY_PREFIX"] == EXTERNAL["CACHE_KEY_PREFIX"]
        assert service["environment"]["BROKER_KEY_PREFIX"] == EXTERNAL["BROKER_KEY_PREFIX"]
        assert "db-bouncer" in service["depends_on"]
    assert services["api"]["environment"]["WORKER"] == "main"
    assert "CACHE_URL" not in services["socket-phoenix"]["environment"]
    assert "redis" not in services["socket-phoenix"]["depends_on"]


@pytest.mark.parametrize("missing", list(EXTERNAL))
def test_external_mode_rejects_partial_configuration(tmp_path, missing):
    selected = selected_args(tmp_path, {k: v for k, v in EXTERNAL.items() if k != missing})
    assert selected.returncode != 0
    assert missing in selected.stderr


def test_default_mode_keeps_bundled_redis(tmp_path):
    selected = selected_args(tmp_path, {})
    assert selected.returncode == 0
    assert ".redis.yaml" in selected.stdout
    assert ".redis-external.yaml" not in selected.stdout


@pytest.mark.parametrize(
    "values,valid",
    [({"REDIS_EXTERNAL_CACHE_URL": EXTERNAL["REDIS_EXTERNAL_CACHE_URL"]}, False), ({}, True), (EXTERNAL, True)],
)
def test_make_respects_compose_selection_before_docker(tmp_path, values, valid):
    make = shutil.which("make")
    if not make:
        pytest.skip("GNU Make is required")
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    scripts = tmp_path / "scripts" / "utils"
    scripts.mkdir(parents=True)
    for name in ("get-compose-args.sh", "utils.sh"):
        shutil.copyfile(ROOT / "scripts" / "utils" / name, scripts / name)
    (tmp_path / ".env").write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    calls = tmp_path / "docker-calls"
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(f"#!/bin/sh\nprintf 'called\\n' >> {shlex.quote(str(calls))}\n")
    docker.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("REDIS_EXTERNAL_") and key not in ("CACHE_KEY_PREFIX", "BROKER_KEY_PREFIX", "ComSpec")
    }
    env["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
    result = subprocess.run(
        [make, "deploy_ui"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=False
    )
    if valid:
        assert result.returncode == 0, result.stderr
        assert calls.exists()
        assert (".redis-external.yaml" in result.stdout) is bool(values)
    else:
        assert result.returncode != 0, result.stdout
        assert "REDIS_EXTERNAL_BROKER_URL" in result.stderr
        assert not calls.exists(), "Docker must not run with an empty selection after configuration failure"
