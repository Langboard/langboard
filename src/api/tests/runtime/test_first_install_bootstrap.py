"""Exercise fresh-install proof and the original Make cutover boundary."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("first_install", ROOT / "scripts/check-first-install.py")
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    environment = {
        "KEY_PROVIDER_TYPE": "openbao-local",
        "CACHE_URL": "redis://:fixture@fixture_redis:6379/0",
        "BROKER_URL": "redis://:fixture@fixture_redis_broker:6379/0",
        "SOCKET_INTERNAL_URL": "http://fixture_socket_phoenix:5690",
        "SOCKET_EDITOR_INTERNAL_URL": "http://fixture_server:8010",
        "BROADCAST_TYPE": "kafka",
        "BROADCAST_URLS": ",".join(f"fixture_kafka{i}:9092" for i in range(3)),
        "MAIN_DATABASE_URL": "postgresql://fixture:fixture@fixture_db_bouncer:6432/master",
        "READONLY_DATABASE_URL": "postgresql://fixture:fixture@fixture_db_bouncer:6432/readonly",
    }
    services = {name: {} for name in ("redis", "redis-broker", "vault", "kafka0", "kafka1", "kafka2")}
    services["api"] = {"environment": environment}
    services["socket-phoenix"] = {"environment": environment.copy()}
    services["db"] = {"volumes": [{"type": "volume", "source": "pgdata", "target": "/bitnami/postgresql"}]}
    result = {
        "name": "fixture",
        "services": services,
        "volumes": {"pgdata": {"name": "fixture_pgdata", "driver": "local"}},
    }
    monkeypatch.setattr(BOOTSTRAP, "docker", lambda *args: "unix:///fixture.sock" if args[0] == "context" else "")
    return result


def test_fresh_bundled_does_not_require_old_api(config, tmp_path):
    assert BOOTSTRAP.first_install(config, tmp_path)


def test_only_exact_init_env_empty_placeholders_are_allowed(config, tmp_path):
    volumes = tmp_path / "docker/volumes"
    volumes.mkdir(parents=True)
    for name in (".vault-credentials", "vault-secret.json"):
        (volumes / name).touch()
    assert BOOTSTRAP.first_install(config, tmp_path)
    (volumes / "retained-empty-file").touch()
    assert not BOOTSTRAP.first_install(config, tmp_path)


@pytest.mark.parametrize("kind", ["container", "volume", "old-volume", "labelled-volume", "local", "editor"])
def test_retained_state_never_selects_fresh_path(config, tmp_path, monkeypatch, kind):
    def docker(*args):
        if kind == "container" and args[:2] == ("ps", "-aq"):
            return "old-api"
        if args[:3] == ("volume", "ls", "--format"):
            return {"volume": "fixture_pgdata", "old-volume": "fixture_kfdata0"}.get(kind, "")
        if kind == "labelled-volume" and args[:3] == ("volume", "ls", "-q"):
            return "custom-retained-volume"
        return "unix:///fixture.sock" if args[0] == "context" else ""

    monkeypatch.setattr(BOOTSTRAP, "docker", docker)
    if kind in ("local", "editor"):
        path = tmp_path / ("local/uploads/file" if kind == "local" else "docker/volumes/socket_editor_sync/state")
        path.parent.mkdir(parents=True)
        path.write_text("retained")
    assert not BOOTSTRAP.first_install(config, tmp_path)


@pytest.mark.parametrize("kind", ["s3", "kafka", "editor", "redis", "remote", "custom-volume", "symlink"])
def test_unknown_shared_state_denies_fresh_path(config, tmp_path, monkeypatch, kind):
    env = config["services"]["api"]["environment"]
    if kind == "s3":
        env["S3_BUCKET_NAME"] = "retained"
    elif kind == "kafka":
        env["BROADCAST_URLS"] = "retained.invalid:9092"
    elif kind == "editor":
        env["SOCKET_EDITOR_INTERNAL_URL"] = "http://retained.invalid:8010"
    elif kind == "redis":
        env["BROKER_URL"] = "redis://retained.invalid:6379"
    elif kind == "remote":
        monkeypatch.setenv("DOCKER_HOST", "ssh://retained.invalid")
    elif kind == "custom-volume":
        config["volumes"]["pgdata"]["driver_opts"] = {"type": "nfs"}
    else:
        (tmp_path / "local").symlink_to(tmp_path / "unknown")
    with pytest.raises(RuntimeError):
        BOOTSTRAP.first_install(config, tmp_path)


def test_external_database_requires_successful_empty_catalog(config, tmp_path, monkeypatch):
    del config["services"]["db"]
    env = config["services"]["api"]["environment"]
    env["MAIN_DATABASE_URL"] = env["READONLY_DATABASE_URL"] = "postgresql://fixture:fixture@127.0.0.1:1/fresh"
    queried = []
    monkeypatch.setattr(BOOTSTRAP, "empty_external_database", lambda url: queried.append(url) or True)
    assert BOOTSTRAP.first_install(config, tmp_path)
    assert queried == [config["services"]["api"]["environment"]["MAIN_DATABASE_URL"]]
    monkeypatch.setattr(BOOTSTRAP, "empty_external_database", lambda url: False)
    assert not BOOTSTRAP.first_install(config, tmp_path)


def test_unreachable_external_database_denied(config, tmp_path):
    del config["services"]["db"]
    env = config["services"]["api"]["environment"]
    env["MAIN_DATABASE_URL"] = env["READONLY_DATABASE_URL"] = "postgresql://fixture:fixture@127.0.0.1:1/fresh"
    with pytest.raises(Exception):
        BOOTSTRAP.first_install(config, tmp_path)


def test_remote_context_overrides_local_host_and_denies_proof(config, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///fixture.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setattr(BOOTSTRAP, "docker", lambda *args: "ssh://retained.invalid" if args[0] == "context" else "")
    with pytest.raises(RuntimeError):
        BOOTSTRAP.first_install(config, tmp_path)


@pytest.mark.parametrize("fresh", [True, False])
def test_make_preserves_retained_cutover_checks(config, tmp_path, fresh):
    # Run the actual Make recipe and proof script with disposable Docker inventory.
    (tmp_path / "Makefile").write_text((ROOT / "Makefile").read_text())
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=fixture\nSOCKET_PHOENIX_INTERNAL_SECRET="
        + "x" * 32
        + "\nBROADCAST_PHOENIX_FANOUT_CONSUMER_GROUP=fixture-fanout\n"
    )
    (tmp_path / "config.json").write_text(json.dumps(config))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport json,sys,pathlib\na=sys.argv[1:]\np=pathlib.Path.cwd()\nwith (p/'calls').open('a') as f: f.write(' '.join(a)+'\\n')\nif a[0]=='info': print('fixture')\nelif a[0]=='compose': print((p/'config.json').read_text())\nelif a[0]=='context': print('unix:///fixture.sock')\nelif a[:2]==['ps','-aq'] and "
        + repr(not fresh)
        + ": print('old-api')\nelif a[0]=='exec':\n print('Error response from daemon: No such container: fixture_api',file=sys.stderr)\n sys.exit(1)\n"
    )
    docker.chmod(0o755)
    uv = bindir / "uv"
    uv.write_text("#!/bin/sh\nexec '" + sys.executable + "' scripts/check-first-install.py\n")
    uv.chmod(0o755)
    result = subprocess.run(
        ["make", "prepare_socket_phoenix_owner", "COMPOSE_ARGS="],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    calls = (tmp_path / "calls").read_text()
    if fresh:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "First installation proven" in result.stdout
        assert "exec -i fixture_api" not in calls
    else:
        assert result.returncode != 0
        assert "exec -i fixture_api" in calls
        assert "No such container" in result.stderr
    recipe = (ROOT / "Makefile").read_text().split("prepare_socket_phoenix_owner: ", 1)[1].split(".PHONY:", 1)[0]
    assert (
        recipe.index("check_socket_phoenix_cutover")
        < recipe.index("require_socket_phoenix_group")
        < recipe.index("check_socket_phoenix_editor_restore")
    )


@pytest.fixture(scope="module")
def disposable_postgres(tmp_path_factory):
    """Private Unix-socket cluster; never uses Docker or a configured database."""
    binaries = Path(shutil.which("initdb") or "/opt/homebrew/opt/postgresql@17/bin/initdb").parent
    if not (binaries / "initdb").is_file():
        pytest.skip("Local PostgreSQL binaries unavailable for disposable catalog proof")
    directory = tmp_path_factory.mktemp("first-install-pg").resolve()
    data = directory / "data"
    socket_directory = tempfile.TemporaryDirectory(prefix="lbpg-", dir="/tmp")
    sock = Path(socket_directory.name).resolve()
    initialized = subprocess.run(
        [str(binaries / "initdb"), "-D", str(data), "-U", "fixture", "-A", "trust", "--no-locale"],
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    started = subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(directory / "postgres.log"),
            "-o",
            f"-F -h '' -k {sock}",
            "-w",
            "start",
        ],
        capture_output=True,
        text=True,
    )
    try:
        assert started.returncode == 0, started.stderr
        yield f"postgresql://fixture@/postgres?host={sock}"
    finally:
        if started.returncode == 0:
            subprocess.run(
                [str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                capture_output=True,
                text=True,
                check=True,
            )
        socket_directory.cleanup()


def test_actual_catalog_empty_retained_hidden_and_type_data(disposable_postgres):
    import psycopg

    url = disposable_postgres
    assert BOOTSTRAP.empty_external_database(url)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("CREATE TABLE retained (id integer)")
        assert not BOOTSTRAP.empty_external_database(url)
        connection.execute("DROP TABLE retained")
        connection.execute("CREATE SCHEMA hidden")
        connection.execute("CREATE TABLE hidden.retained (id integer)")
        connection.execute("CREATE ROLE catalog_probe LOGIN")
        restricted_url = url.replace("fixture@", "catalog_probe@")
        assert not BOOTSTRAP.empty_external_database(restricted_url)
        connection.execute("DROP SCHEMA hidden CASCADE")
        connection.execute("CREATE TYPE retained_type AS ENUM ('retained')")
        assert not BOOTSTRAP.empty_external_database(url)
        connection.execute("DROP TYPE retained_type")
    assert BOOTSTRAP.empty_external_database(url)


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("otel", [False, True])
def test_actual_compose_generated_runtime_proves_fresh_storage(tmp_path, monkeypatch, external, otel):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose unavailable")
    import shlex

    shutil.copytree(ROOT / "docker", tmp_path / "docker", ignore=shutil.ignore_patterns("volumes", ".*.env"))
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    values = {
        "PROJECT_NAME": "fixture",
        "COMMON_SECRET_KEY": "fixture-signing-key",
        "JWT_SECRET_KEY": "fixture-jwt-key",
        "SOCKET_PHOENIX_INTERNAL_SECRET": "x" * 32,
        "MAIL_SERVER": "smtp.invalid",
        "MAIL_FROM": "fixture@example.invalid",
        "MAIL_PORT": "25",
    }
    if external:
        values["POSTGRES_EXTERNAL_MAIN_URL"] = "postgresql://fixture:fixture@external.invalid:5432/fresh"
    lines = (ROOT / ".env.example").read_text().splitlines()
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}={values[key]}" if (key := line.split("=", 1)[0]) in values else line for line in lines)
    )
    fixture_keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    env = {key: value for key, value in os.environ.items() if key not in fixture_keys}
    env.update(WITH_DB_BACKUP="true", WITH_OTEL=str(otel).lower())
    generated = subprocess.run(
        ["bash", "scripts/utils/update-docker-envs.sh"], cwd=tmp_path, env=env, text=True, capture_output=True
    )
    assert generated.returncode == 0, generated.stderr
    selected = subprocess.run(
        ["bash", "scripts/utils/get-compose-args.sh"], cwd=tmp_path, env=env, text=True, capture_output=True
    )
    assert selected.returncode == 0, selected.stderr
    rendered = subprocess.run(
        ["docker", "compose", *shlex.split(selected.stdout), "config", "--format", "json"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(BOOTSTRAP, "docker", lambda *args: "unix:///fixture.sock" if args[0] == "context" else "")
    queried = []
    monkeypatch.setattr(BOOTSTRAP, "empty_external_database", lambda url: queried.append(url) or True)
    assert BOOTSTRAP.first_install(json.loads(rendered.stdout), tmp_path.resolve())
    assert queried == ([values["POSTGRES_EXTERNAL_MAIN_URL"]] if external else [])
