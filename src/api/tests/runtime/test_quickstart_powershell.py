"""Run the Windows entry points with inert native PowerShell command doubles."""

import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]


def powershell():
    shell = shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required for executable quickstart checks")
    return shell


@pytest.mark.parametrize(
    "script,mode",
    [
        ("quickstart.ps1", []),
        ("quickstart-ollama-cpu.ps1", ["ollama-cpu"]),
        ("quickstart-ollama-gpu.ps1", ["ollama-gpu"]),
    ],
)
@pytest.mark.parametrize("location", ["root", "scripts", "unrelated"])
def test_entrypoint_uses_script_location_and_preserves_mode_and_failure(tmp_path, script, mode, location):
    shell = powershell()
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / script, scripts / script)
    (scripts / "run_docker_compose.ps1").write_text(
        "ConvertTo-Json -InputObject @($args) | Set-Content $env:QUICKSTART_RECEIPT\nexit 23\n"
    )
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(scripts / script)],
        cwd={"root": root, "scripts": scripts, "unrelated": unrelated}[location],
        env={**os.environ, "QUICKSTART_RECEIPT": str(tmp_path / "receipt.json")},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert json.loads((tmp_path / "receipt.json").read_text()) == mode


@pytest.mark.parametrize("exit_code", [0, 23])
@pytest.mark.parametrize("existing_env", [False, True])
def test_runner_resolves_checkout_preserves_environment_and_make_failure(tmp_path, exit_code, existing_env):
    shell = powershell()
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/run_docker_compose.ps1", scripts / "run_docker_compose.ps1")
    (root / ".env.example").write_text("PROJECT_NAME=example\n")
    if existing_env:
        (root / ".env").write_text("PROJECT_NAME=preserved\n")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "function docker { $global:LASTEXITCODE = 0 }\n"
        "function make {\n"
        "  @{ cwd=(Get-Location).Path; argv=@($args) } | ConvertTo-Json | Set-Content $env:QUICKSTART_RECEIPT\n"
        f"  $global:LASTEXITCODE = {exit_code}\n"
        "}\nfunction Pause {}\n"
        '& $env:QUICKSTART_RUNNER "ollama-gpu"\nexit $LASTEXITCODE\n'
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(harness)],
        cwd=tmp_path,
        env={
            **os.environ,
            "QUICKSTART_RECEIPT": str(tmp_path / "receipt.json"),
            "QUICKSTART_RUNNER": str(scripts / "run_docker_compose.ps1"),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert Path(receipt["cwd"]) == root
    assert "WITH_OLLAMA_GPU=true" in receipt["argv"]
    assert "WITH_OLLAMA_CPU=false" in receipt["argv"]
    assert (root / ".env").read_text() == ("PROJECT_NAME=preserved\n" if existing_env else "PROJECT_NAME=example\n")
