"""Generate real service environments in a disposable checkout, without services."""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]
REQUIRED = {
    "SOCKET_PHOENIX_INTERNAL_SECRET": "fixture-secret-" * 3,
    "MAIL_SERVER": "smtp.invalid",
    "MAIL_FROM": "fixture@example.invalid",
    "MAIL_PORT": "587",
}


def bootstrap(tmp_path, shell_name, values):
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} is required for executable environment bootstrap checks")
    scripts = tmp_path / "scripts/utils"
    templates = tmp_path / "docker/envs"
    scripts.mkdir(parents=True)
    templates.mkdir(parents=True)
    for name in ("update-docker-envs.sh", "update-docker-envs.ps1", "utils.sh"):
        shutil.copyfile(ROOT / "scripts/utils" / name, scripts / name)
    for source in (ROOT / "docker/envs").glob("*.env.template"):
        shutil.copyfile(source, templates / source.name)
    (tmp_path / ".env").write_text(
        (ROOT / ".env.example").read_text() + "\n" + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
    )
    command = [shell, str(scripts / "update-docker-envs.sh")]
    if shell_name == "pwsh":
        command = [shell, "-NoProfile", "-File", str(scripts / "update-docker-envs.ps1")]
    # No caller environment may override fixture credentials or database selection.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "SystemRoot")}
    return subprocess.run(command, cwd=tmp_path.parent, env=env, capture_output=True, text=True, timeout=20), templates


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("database", ["bundled", "external", "replica"])
def test_readme_configuration_generates_service_urls(tmp_path, shell, database):
    values = dict(REQUIRED)
    if database != "bundled":
        values["POSTGRES_EXTERNAL_MAIN_URL"] = "postgresql://fixture:fixture@primary.invalid:5432/main"
    if database == "replica":
        values["POSTGRES_EXTERNAL_REPLICA_URL"] = "postgresql://fixture:fixture@replica.invalid:5432/main"
    result, templates = bootstrap(tmp_path, shell, values)
    assert result.returncode == 0, result.stderr
    assert {p.name for p in templates.glob(".*.env")} == {
        ".nginx.env",
        ".api.env",
        ".ui.env",
        ".socket.env",
        ".graph.env",
        ".db_backup.env",
    }
    for service in ("api", "socket", "graph"):
        generated = dict(line.split("=", 1) for line in (templates / f".{service}.env").read_text().splitlines())
        assert generated["MAIN_DATABASE_URL"] == values.get(
            "POSTGRES_EXTERNAL_MAIN_URL", "postgresql://postgres:postgres@langboard_db_bouncer:6432/master"
        )
        assert generated["READONLY_DATABASE_URL"] == values.get(
            "POSTGRES_EXTERNAL_REPLICA_URL",
            values.get(
                "POSTGRES_EXTERNAL_MAIN_URL", "postgresql://postgres:postgres@langboard_db_bouncer:6432/readonly"
            ),
        )
        assert generated["MAIL_SERVER"] == REQUIRED["MAIL_SERVER"]
        # Phoenix credentials are injected by Compose environment, not these files.
        assert generated["MAX_REQUEST_BODY_SIZE_BYTES"] == str(51 * 1024 * 1024)

    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required to verify resolved service environments")
    selection = subprocess.run(
        [shutil.which("bash"), str(ROOT / "scripts/utils/get-compose-args.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert selection.returncode == 0, selection.stderr
    args = shlex.split(selection.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            shutil.copyfile(ROOT / value, tmp_path / value)
    composed = subprocess.run(
        ["docker", "compose", *args, "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert composed.returncode == 0, composed.stderr
    services = json.loads(composed.stdout)["services"]
    for name in ("api", "celeryworker", "graph", "socket-phoenix"):
        assert (
            services[name]["environment"]["SOCKET_PHOENIX_INTERNAL_SECRET"]
            == REQUIRED["SOCKET_PHOENIX_INTERNAL_SECRET"]
        )
    for name in ("api", "celeryworker", "graph"):
        assert services[name]["environment"]["MAIN_DATABASE_URL"] == generated["MAIN_DATABASE_URL"]
        assert services[name]["environment"]["READONLY_DATABASE_URL"] == generated["READONLY_DATABASE_URL"]


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_configuration_fails_before_generating_service_envs(tmp_path, shell, missing):
    result, templates = bootstrap(tmp_path, shell, {**REQUIRED, missing: ""})
    assert result.returncode != 0
    assert missing in result.stderr
    assert not list(templates.glob(".*.env"))
