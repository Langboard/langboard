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
