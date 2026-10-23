"""Exercise the install selector and real Compose merge without starting services."""

import json
import shlex
import shutil
import subprocess
import pytest
from test_external_redis_compose import ROOT, selected_args


@pytest.mark.parametrize("replica", [False, True])
def test_external_postgres_selection_has_no_stdout_diagnostics(tmp_path, replica):
    values = {"POSTGRES_EXTERNAL_MAIN_URL": "postgresql://fixture:fixture@primary.invalid:5432/main"}
    if replica:
        values["POSTGRES_EXTERNAL_REPLICA_URL"] = "postgresql://fixture:fixture@replica.invalid:5432/main"
    selected = selected_args(tmp_path, values)
    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.startswith("-f ")
    assert "valid" not in selected.stdout
    assert "POSTGRES_EXTERNAL_MAIN_URL is valid" in selected.stderr
    assert ".pg.yaml" not in selected.stdout


@pytest.mark.parametrize("external", [False, True])
def test_postgres_dependency_matches_selected_database(tmp_path, external):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    values = (
        {"POSTGRES_EXTERNAL_MAIN_URL": "postgresql://fixture:fixture@primary.invalid:5432/main"} if external else {}
    )
    selected = selected_args(tmp_path, values)
    assert selected.returncode == 0, selected.stderr
    args = shlex.split(selected.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            args[index] = str(ROOT / value)
    rendered = subprocess.run(
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
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    assert ("db-bouncer" in services) is not external
    for name in ("api", "celeryworker", "graph"):
        dependencies = services[name]["depends_on"]
        assert ("db-bouncer" in dependencies) is not external
        if not external:
            assert dependencies["db-bouncer"]["condition"] == "service_healthy"
        for dependency in ("kafka0", "kafka1", "kafka2", "redis", "redis-broker"):
            assert dependencies[dependency]["condition"] == "service_healthy"


def test_invalid_external_postgres_fails_without_emitting_compose_args(tmp_path):
    selected = selected_args(tmp_path, {"POSTGRES_EXTERNAL_MAIN_URL": "https://private.invalid/secret"})
    assert selected.returncode != 0
    assert selected.stdout == ""
    assert "POSTGRES_EXTERNAL_MAIN_URL must start with" in selected.stderr
    assert "private.invalid" not in selected.stderr


def test_postgres_warning_keeps_stdout_empty_on_declined_confirmation(tmp_path):
    result = subprocess.run(
        [
            shutil.which("bash"),
            "-c",
            'source "$1"; validate_postgres_url "$2" POSTGRES_EXTERNAL_MAIN_URL',
            "validate",
            str(ROOT / "scripts/utils/utils.sh"),
            "postgresql://fixture@primary.invalid/main",
        ],
        input="n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Warning: POSTGRES_EXTERNAL_MAIN_URL" in result.stderr
    assert "primary.invalid" not in result.stderr


@pytest.mark.parametrize(
    "url,valid",
    [
        ("postgresql://fixture:fixture@primary.invalid:5432/main", True),
        ("https://private.invalid/secret", False),
        ("postgresql://fixture@primary.invalid/main", False),
    ],
)
def test_powershell_postgres_diagnostics_do_not_pollute_compose_arguments(tmp_path, url, valid):
    shell = shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required for executable PostgreSQL selection checks")
    selected_args(tmp_path, {"POSTGRES_EXTERNAL_MAIN_URL": url})
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(ROOT / "scripts/utils/get-compose-args.ps1"), "-WithDbBackup", "false"],
        cwd=tmp_path,
        input="n\n",
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert (result.returncode == 0) is valid
    assert "POSTGRES_EXTERNAL_MAIN_URL" in result.stderr
    assert "private.invalid" not in result.stderr
    assert "primary.invalid" not in result.stderr
    if valid:
        assert result.stdout.startswith("-f\n")
        assert "valid" not in result.stdout
        assert ".pg.yaml" not in result.stdout
    else:
        assert result.stdout == ""
