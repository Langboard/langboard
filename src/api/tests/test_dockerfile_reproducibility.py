import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_shared_python_lock_matches_its_build_manifest() -> None:
    """The first locked Docker sync must work in a clean build context."""

    subprocess.run(
        ["uv", "lock", "--check", "--directory", str(ROOT / "src" / "shared" / "py")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_python_and_uv_images_are_digest_pinned_without_remote_installer() -> None:
    """The API image uses immutable runtime/tool inputs and no mutable install script."""

    dockerfile = (ROOT / "Dockerfile").read_text()
    lines = dockerfile.splitlines()

    assert re.fullmatch(
        r"FROM ghcr\.io/astral-sh/uv:0\.11\.28@sha256:[0-9a-f]{64} AS uv",
        lines[0],
    )
    assert re.fullmatch(
        r"FROM python:3\.12@sha256:[0-9a-f]{64} AS base",
        lines[1],
    )
    assert "astral.sh/uv/install.sh" not in dockerfile
    assert "RUN cron" not in dockerfile
    sync_commands = [line for line in lines if "uv sync " in line]
    assert sync_commands
    assert all("uv sync --locked --no-dev" in line for line in sync_commands)


def test_api_runtime_excludes_document_processing_extra() -> None:
    """Install Docling only in the broker worker image, never in API workers."""

    pyproject = (ROOT / "pyproject.toml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker" / "docker-compose.server.yaml").read_text()
    example_environment = (ROOT / ".env.example").read_text()

    project = tomllib.loads(pyproject)["project"]
    shared = tomllib.loads((ROOT / "src/shared/py/pyproject.toml").read_text())["project"]
    assert not any(dependency.startswith(("docling>=", "docling-slim[")) for dependency in project["dependencies"])
    assert project["optional-dependencies"]["document-processing"] == ["langboard-shared[document-processing]"]
    assert not any(dependency.startswith("docling-slim[") for dependency in shared["dependencies"])
    assert any(
        dependency.startswith("docling-slim[") for dependency in shared["optional-dependencies"]["document-processing"]
    )
    assert "FROM base AS with-document-processing" in dockerfile
    assert "uv sync --locked --no-dev --extra document-processing" in dockerfile
    assert "target: with-document-processing" in compose
    assert "API_WORKERS_COUNT=2" in example_environment


def test_compose_cron_target_uses_safe_dockerfile_default() -> None:
    """The cron image installs a valid empty table without a phantom build argument."""

    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker" / "docker-compose.server.yaml").read_text()

    assert "ARG CRON_TAB_FILE" not in dockerfile
    assert "printf '' | crontab -" in dockerfile
    assert "RUN cron" not in dockerfile
    assert "target: with-cron" in compose
    assert "CRON_TAB_FILE:" not in compose


def test_ui_build_refreshes_the_mounted_shared_package_first() -> None:
    """A UI rebuild must not retain stale routing constants from its image layer."""

    compose = (ROOT / "docker" / "docker-compose.server.yaml").read_text()
    ui_service = compose.split("\n  ui:", maxsplit=1)[1].split("\n  api:", maxsplit=1)[0]

    assert (
        'command: sh -c "cd /shared/ts && yarn build && cd /app && yarn link @langboard/core && yarn build"'
        in ui_service
    )
    assert "../src/shared/ts/src:/shared/ts/src" in ui_service
    assert "../src/shared/ts:/shared/ts" not in ui_service


def test_environment_renderer_is_release_worktree_safe() -> None:
    """Resolve the repository root from the script, not its checkout basename."""

    renderer = (ROOT / "scripts" / "utils" / "update-docker-envs.sh").read_text()

    assert renderer.startswith("#!/bin/bash\nset -e\n")
    assert 'ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"' in renderer
    assert 'cd "$ROOT_DIR"' in renderer
    assert 'CURRENT_DIR=$(basename "$PWD")' not in renderer
    assert '== "langboard"' not in renderer


def test_server_environment_exports_oidc_resource_server_settings() -> None:
    """Container recreation must preserve delegated bearer authentication."""

    template = (ROOT / "docker" / "envs" / "server.env.template").read_text()

    assert "OIDC_BEARER_ENABLED=${OIDC_BEARER_ENABLED}" in template
    assert "OIDC_RESOURCE_AUDIENCE=${OIDC_RESOURCE_AUDIENCE}" in template


def test_runtime_image_packages_scheduled_worker_entrypoints() -> None:
    """GHCR deployments must not depend on host checkouts for scheduled workers."""

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY ./scripts/run_*_cron.sh ./scripts/run_*_recovery.sh ./scripts/" in dockerfile
    scripts = sorted((ROOT / "scripts").glob("run_*_cron.sh")) + sorted((ROOT / "scripts").glob("run_*_recovery.sh"))
    assert (ROOT / "scripts" / "run_app_event_cron.sh") in scripts
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True, text=True)
    compose = (ROOT / "docker" / "docker-compose.server.yaml").read_text()
    assert "../scripts/run_app_event_cron.sh:/app/scripts/run_app_event_cron.sh:x" in compose
