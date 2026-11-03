"""Exercise public shell entry points without Docker or deployment writes."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "script,cpu,gpu",
    [
        ("quickstart.sh", "false", "false"),
        ("quickstart-ollama-cpu.sh", "true", "false"),
        ("quickstart-ollama-gpu.sh", "false", "true"),
    ],
)
@pytest.mark.parametrize("location", ["root", "scripts", "unrelated"])
@pytest.mark.parametrize("existing_env", [False, True])
def test_quickstart_preserves_mode_environment_and_failure_from_any_location(
    tmp_path, script, cpu, gpu, location, existing_env
):
    checkout = tmp_path / "custom checkout with spaces"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in (script, "run_docker_compose.sh"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
        (scripts / name).chmod(0o755)
    (checkout / ".env.example").write_text("PROJECT_NAME=example\n")
    if existing_env:
        (checkout / ".env").write_text("PROJECT_NAME=preserved\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "docker").write_text("#!/bin/sh\nexit 0\n")
    receipt = tmp_path / "receipt"
    (binary / "make").write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > ' + shlex.quote(str(receipt)) + "\nexit 23\n")
    for name in ("docker", "make"):
        (binary / name).chmod(0o755)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    result = subprocess.run(
        [shutil.which("bash"), str(scripts / script)],
        cwd={"root": checkout, "scripts": scripts, "unrelated": unrelated}[location],
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    argv = receipt.read_text().splitlines()
    assert Path(argv[0]) == checkout
    assert "start_docker" in argv
    assert f"WITH_OLLAMA_CPU={cpu}" in argv
    assert f"WITH_OLLAMA_GPU={gpu}" in argv
    assert (checkout / ".env").read_text() == ("PROJECT_NAME=preserved\n" if existing_env else "PROJECT_NAME=example\n")
    assert not (unrelated / ".env").exists()
