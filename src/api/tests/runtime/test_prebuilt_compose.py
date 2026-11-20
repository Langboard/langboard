"""Render real production Compose; no daemon, image pull or live data is used."""

import json
import os
import re
import shutil
import subprocess
import pytest
from test_external_redis_compose import ROOT


def test_colocated_profile_tests_survive_container_context_exclusions():
    targets = set(
        re.findall(r"(?:src|scripts)/[\w/.-]+_test\.py", (ROOT / "scripts/run-profile-validation.sh").read_text())
    )
    patterns = set((ROOT / ".dockerignore").read_text().splitlines())
    assert all((ROOT / target).is_file() for target in targets)
    if "**/*_test.py" in patterns:
        assert not {target for target in targets if f"!{target}" not in patterns}


def render(tmp_path, *, overlays=(), profiles=("application", "workers"), missing=None):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    runtime = tmp_path / "runtime.env"
    runtime.write_text("PRIVATE_TEST_VALUE=not-for-output\n")
    secret = tmp_path / "existing-key"
    secret.write_text("disposable-fixture\n")
    values = {
        **{
            f"LANGBOARD_{kind}_IMAGE": f"registry.example/board-{kind.lower()}@sha256:" + "a" * 64
            for kind in ("API", "WORKER", "UI", "GRAPH")
        },
        **{f"LANGBOARD_{kind}_ENV_FILE": str(runtime) for kind in ("API", "WORKER", "GRAPH")},
        "LANGBOARD_LOCAL_VOLUME": "existing-application-data",
        "LANGBOARD_OAUTH_VOLUME": "existing-oauth-state",
        "LANGBOARD_DEPENDENCY_NETWORK": "existing-dependencies",
        "LANGBOARD_VAULT_CREDENTIALS_FILE": str(secret),
        "LANGBOARD_IDENTITY_SIGNING_KEY_FILE": str(secret),
        "LANGBOARD_UI_BIND": "127.0.0.1:1000",
        "LANGBOARD_API_BIND": "127.0.0.1:1002",
        "SOCKET_URL": "wss://realtime.example.invalid",
        "SOCKET_INTERNAL_URL": "https://realtime-internal.example.invalid",
        "SOCKET_EDITOR_INTERNAL_URL": "https://editor-internal.example.invalid",
    }
    if missing:
        del values[missing]
    # Do not consume an operator's current .env or ambient deployment variables.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("LANGBOARD_", "COMPOSE_", "SOCKET_"))
    }
    environment.update(values)
    args = ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(tmp_path / "empty.env")]
    (tmp_path / "empty.env").write_text("")
    for profile in profiles:
        args += ["--profile", profile]
    for name in ("prebuilt", *overlays):
        args += ["-f", str(ROOT / f"docker/docker-compose.{name}.yaml")]
    return subprocess.run(
        [*args, "config", "--format", "json"], env=environment, capture_output=True, text=True, timeout=30
    )


def test_prebuilt_images_and_existing_state_without_development_or_dependency_services(tmp_path):
    result = render(tmp_path)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert set(config["services"]) == {"api", "worker", "graph", "ui"}
    for service in config["services"].values():
        assert "@sha256:" in service["image"]
        assert "build" not in service and "container_name" not in service and "ports" not in service
        for volume in service.get("volumes", []):
            if volume["target"] == "/run/langboard/kafka":
                assert volume["type"] == "bind" and volume["read_only"] is True
            else:
                assert volume["type"] == "volume"
        if service is not config["services"]["ui"]:
            assert any(volume["target"] == "/run/langboard/kafka" for volume in service["volumes"])
    assert config["volumes"]["local-data"] == {"name": "existing-application-data", "external": True}
    assert config["volumes"]["mcp-oauth-state"] == {"name": "existing-oauth-state", "external": True}
    assert config["networks"]["dependencies"]["name"] == "existing-dependencies"
    assert config["networks"]["dependencies"]["external"] is True
    api = config["services"]["api"]
    worker = config["services"]["worker"]
    assert api["environment"]["WORKER"] == "main"
    assert worker["environment"]["WORKER"] == "broker"
    assert api["environment"]["FASTMCP_HOME"] == worker["environment"]["FASTMCP_HOME"] == "/app/.fastmcp"
    assert api["healthcheck"]["interval"] == "30s"
    assert worker["profiles"] == ["workers"]
    assert api["profiles"] == config["services"]["graph"]["profiles"] == ["application"]
    assert api["command"][-1] == "2"
    assert worker["command"][-1] == "run:broker"


def test_prebuilt_optional_native_secrets_and_existing_ingress(tmp_path):
    result = render(tmp_path, overlays=("prebuilt-vault", "prebuilt-identity", "prebuilt-ports"))
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    for name in ("api", "worker", "graph"):
        secrets = config["services"][name]["secrets"]
        assert {"source": "vault-credentials", "target": "/app/.vault-credentials"} in secrets
    api = config["services"]["api"]
    assert api["environment"]["IDENTITY_PROOF_SIGNING_KEY_PATH"] == "/run/secrets/identity-proof-signing-key"
    assert {"source": "identity-proof-signing-key", "target": "/run/secrets/identity-proof-signing-key"} in api[
        "secrets"
    ]
    assert api["ports"][0]["host_ip"] == "127.0.0.1"
    assert str(api["ports"][0]["published"]) == "1002"
    assert config["services"]["ui"]["ports"][0]["target"] == 8080


@pytest.mark.parametrize(
    "missing",
    [
        "LANGBOARD_API_IMAGE",
        "LANGBOARD_LOCAL_VOLUME",
        "LANGBOARD_OAUTH_VOLUME",
        "LANGBOARD_DEPENDENCY_NETWORK",
        "SOCKET_INTERNAL_URL",
    ],
)
def test_prebuilt_requires_explicit_images_and_preservation_identities(tmp_path, missing):
    result = render(tmp_path, missing=missing)
    assert result.returncode != 0
    assert missing in result.stderr
    assert "not-for-output" not in result.stderr


def test_prebuilt_does_not_start_owners_without_explicit_profiles(tmp_path):
    result = render(tmp_path, profiles=())
    assert result.returncode == 0, result.stderr
    assert not json.loads(result.stdout).get("services")
