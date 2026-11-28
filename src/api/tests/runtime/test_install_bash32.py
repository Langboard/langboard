"""Run the Unix install boundaries with macOS's stock /bin/bash in copied fixtures."""

import json
import os
import re
import shlex
import shutil
import subprocess
import pytest
from test_install_environment_bootstrap import REQUIRED, ROOT, bootstrap


BASH = "/bin/bash"
MAPPINGS = {
    "nginx": ("server-common",),
    "api": ("server-common", "server"),
    "ui": ("server-common",),
    "socket": ("server-common", "server"),
    "graph": ("server-common", "server"),
    "db_backup": ("db-backup",),
}


def select(tmp_path, values, answer=""):
    scripts = tmp_path / "scripts/utils"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("get-compose-args.sh", "utils.sh"):
        shutil.copyfile(ROOT / "scripts/utils" / name, scripts / name)
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
    return subprocess.run(
        [BASH, str(scripts / "get-compose-args.sh")],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "TMPDIR")},
        input=answer,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("database", ["bundled", "external", "replica"])
def test_stock_bash_generates_exact_service_templates_and_preserves_env(tmp_path, database):
    values = dict(REQUIRED)
    if database != "bundled":
        values["POSTGRES_EXTERNAL_MAIN_URL"] = "postgresql://fixture:fixture@primary.invalid:5432/main"
    if database == "replica":
        values["POSTGRES_EXTERNAL_REPLICA_URL"] = "postgresql://fixture:fixture@replica.invalid:5432/main"
    result, templates = bootstrap(tmp_path, BASH, values)
    assert result.returncode == 0, result.stderr
    expected_diagnostics = ""
    if database != "bundled":
        expected_diagnostics += "✓ POSTGRES_EXTERNAL_MAIN_URL is valid\n"
    if database == "replica":
        expected_diagnostics += "✓ POSTGRES_EXTERNAL_REPLICA_URL is valid\n"
    assert result.stderr == expected_diagnostics
    assert {path.name for path in templates.glob(".*.env")} == {f".{service}.env" for service in MAPPINGS}
    for service, sources in MAPPINGS.items():
        expected_keys = [
            match.group(1)
            for source in sources
            for line in (templates / f"{source}.env.template").read_text().splitlines()
            if (match := re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line))
        ]
        lines = (templates / f".{service}.env").read_text().splitlines()
        assert [line.split("=", 1)[0] for line in lines] == expected_keys
        assert f"Generating docker/envs/.{service}.env from templates: {' '.join(sources)}" in result.stdout
        generated = dict(line.split("=", 1) for line in lines)
        if service in ("api", "socket", "graph"):
            assert generated["MAIN_DATABASE_URL"] == values.get(
                "POSTGRES_EXTERNAL_MAIN_URL", "postgresql://postgres:postgres@langboard_db_bouncer:6432/master"
            )
            assert generated["READONLY_DATABASE_URL"] == values.get(
                "POSTGRES_EXTERNAL_REPLICA_URL",
                values.get(
                    "POSTGRES_EXTERNAL_MAIN_URL", "postgresql://postgres:postgres@langboard_db_bouncer:6432/readonly"
                ),
            )
    expected_env = (ROOT / ".env.example").read_text() + "\n" + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
    assert (tmp_path / ".env").read_text() == expected_env

    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    shutil.copyfile(ROOT / "scripts/utils/get-compose-args.sh", tmp_path / "scripts/utils/get-compose-args.sh")
    selected = subprocess.run(
        [BASH, str(tmp_path / "scripts/utils/get-compose-args.sh")],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert selected.returncode == 0, selected.stderr
    args = shlex.split(selected.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            shutil.copyfile(ROOT / value, tmp_path / value)
    rendered = subprocess.run(
        ["docker", "compose", *args, "config", "--format", "json"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    assert ("db-bouncer" in services) is (database == "bundled")
    for name in ("api", "celeryworker", "graph", "socket-phoenix"):
        assert (
            services[name]["environment"]["SOCKET_PHOENIX_INTERNAL_SECRET"]
            == REQUIRED["SOCKET_PHOENIX_INTERNAL_SECRET"]
        )


@pytest.mark.parametrize("backup", ["true", "TRUE", "TrUe", "false", "FaLsE"])
@pytest.mark.parametrize("external", [False, True])
def test_stock_bash_selector_normalizes_case_and_preserves_exact_arguments(tmp_path, backup, external):
    values = {
        "WITH_DB_BACKUP": backup,
        "KEY_PROVIDER_TYPE": "OpEnBaO-LoCaL",
        "SOCKET_DEPLOYMENT_MODE": "BuNdLeD",
    }
    if external:
        values["POSTGRES_EXTERNAL_MAIN_URL"] = "postgresql://fixture:fixture@primary.invalid:5432/main"
    result = select(tmp_path, values)
    assert result.returncode == 0, result.stderr
    assert ("POSTGRES_EXTERNAL_MAIN_URL is valid" in result.stderr) is external
    assert "bad substitution" not in result.stderr
    names = ["", ".kafka"]
    if not external:
        names.append(".pg")
    if backup.lower() == "true":
        names.append(".backup")
    names.extend([".redis", ".server"])
    expected = [token for name in names for token in ("-f", f"./docker/docker-compose{name}.yaml")]
    expected.extend(["--env-file", "./.env", "-f", "./docker/docker-compose.vault.yaml"])
    assert shlex.split(result.stdout) == expected


def test_stock_bash_external_socket_case_and_nonlocal_vault(tmp_path):
    result = select(
        tmp_path,
        {
            "SOCKET_DEPLOYMENT_MODE": "ExTeRnAl",
            "SOCKET_URL": "wss://socket.invalid",
            "SOCKET_INTERNAL_URL": "https://socket.invalid",
            "SOCKET_EDITOR_INTERNAL_URL": "https://socket.invalid/editor",
            "KEY_PROVIDER_TYPE": "aws",
            "WITH_OTEL": "true",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert ".phoenix-external.yaml" in result.stdout
    assert ".otel.yaml" not in result.stdout
    assert ".vault.yaml" not in result.stdout


@pytest.mark.parametrize(
    "setting,value,message",
    [
        ("SOCKET_PHOENIX_INTERNAL_SECRET", "short", "must contain at least 32 characters"),
        ("MAIL_SERVER", "", "MAIL_SERVER is required"),
        ("MAIL_PORT", "0", "MAIL_PORT must be a positive integer"),
        ("MAX_FILE_SIZE_MB", "0", "MAX_FILE_SIZE_MB must be a positive integer"),
        ("SOCKET_PHOENIX_KAFKA_ENABLED", "false", "must be enabled"),
        ("NOTIFICATION_EMAIL_OUTBOX_ENABLED", "false", "must be enabled"),
        ("POSTGRES_EXTERNAL_MAIN_URL", "https://private.invalid/secret", "must start with"),
    ],
)
def test_stock_bash_generation_preserves_validation_errors(tmp_path, setting, value, message):
    result, templates = bootstrap(tmp_path, BASH, {**REQUIRED, setting: value})
    assert result.returncode == 1
    assert message in result.stderr
    assert "private.invalid" not in result.stderr
    assert not list(templates.glob(".*.env"))


@pytest.mark.parametrize(
    "url,answer,code",
    [
        ("https://private.invalid/secret", "", 1),
        ("postgresql://fixture@primary.invalid/main", "n", 1),
        ("postgresql://fixture@primary.invalid/main", "y", 0),
    ],
)
def test_stock_bash_selector_preserves_url_diagnostics_and_exit_codes(tmp_path, url, answer, code):
    result = select(tmp_path, {"POSTGRES_EXTERNAL_MAIN_URL": url}, answer)
    assert result.returncode == code, result.stderr
    assert "POSTGRES_EXTERNAL_MAIN_URL" in result.stderr
    assert "private.invalid" not in result.stderr
    assert "primary.invalid" not in result.stderr
    if code:
        assert result.stdout == ""
    else:
        assert result.stdout.startswith("-f ")
        assert "valid" not in result.stdout
