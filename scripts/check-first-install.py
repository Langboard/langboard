"""Read-only first-install proof. Exit 10 selects the existing cutover checks.

Absence of an API container alone never proves a fresh installation. Unknown
storage or unavailable evidence fails closed; no operator bypass is provided.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


RETAINED = 10
DATA_TARGETS = {
    "/app/local",
    "/local/editor-sync",
    "/data",
    "/openbao/file",
    "/openbao/logs",
    "/bao/config",
    "/openbao/.vault-credentials",
    "/openbao/vault-secret.json",
    "/bitnami/postgresql",
    "/var/lib/kafka/data",
}
EMPTY_DATABASE_SQL = """
SELECT NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
) AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_type t
    JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
) AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
) AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace
    WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public')
      AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp%'
)
"""


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        # Docker/Compose errors may contain resolved credentials. Do not echo them.
        raise RuntimeError("Docker evidence unavailable; first-install proof denied")
    return result.stdout


def has_data(path, empty_credentials):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RuntimeError("Symlinked persistent storage requires retained-install checks")
    if not path.exists():
        return False
    if path.is_dir():
        return any(has_data(child, empty_credentials) for child in path.iterdir())
    if path.is_file() and path in empty_credentials and path.stat().st_size == 0:
        return False
    return True


def empty_external_database(url):
    import psycopg

    # Catalogs expose objects even when the connecting role cannot SELECT their
    # rows. An empty table (including alembic_version) still denotes retained state.
    with psycopg.connect(
        url, connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=5000"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(EMPTY_DATABASE_SQL)
            return cursor.fetchone() == (True,)


def first_install(config, root):
    services = config["services"]
    project = config["name"]
    if not project or not services:
        raise RuntimeError("Resolved Compose project is required")
    if docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").strip():
        return False
    # Also catch legacy/unlabelled containers with the configured runtime names.
    containers = set(docker("ps", "-a", "--format", "{{.Names}}").splitlines())
    if containers.intersection(service.get("container_name") for service in services.values()):
        return False
    volumes = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    if any(name.startswith(project + "_") for name in volumes):
        return False
    if docker("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}").strip():
        return False
    for volume in config.get("volumes", {}).values():
        if volume.get("name") in volumes:
            return False
    # Docker gives DOCKER_CONTEXT precedence over DOCKER_HOST.
    if context := os.environ.get("DOCKER_CONTEXT"):
        endpoint = docker("context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}").strip()
    else:
        endpoint = (
            os.environ.get("DOCKER_HOST")
            or docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}").strip()
        )
    if not endpoint.startswith("unix://"):
        raise RuntimeError("A local Docker daemon is required to inspect bind storage")
    empty_credentials = {root / "docker/volumes" / name for name in (".vault-credentials", "vault-secret.json")}
    for path in (root / "local", root / "docker/volumes"):
        if has_data(path, empty_credentials):
            return False
    for service in services.values():
        for mount in service.get("volumes", []):
            if mount["type"] == "volume":
                definition = config["volumes"][mount["source"]]
                if (
                    definition.get("external")
                    or definition.get("driver", "local") != "local"
                    or definition.get("driver_opts")
                ):
                    raise RuntimeError("External or custom volume storage cannot prove a first installation")
            elif mount["target"] in DATA_TARGETS:
                if mount["type"] != "bind":
                    raise RuntimeError("Unknown persistent storage cannot prove a first installation")
                if has_data(Path(mount["source"]), empty_credentials):
                    return False
    # External queue/editor/key-provider state needs its original cutover proof.
    if not {"redis", "redis-broker", "socket-phoenix", "vault", "kafka0", "kafka1", "kafka2"}.issubset(services):
        raise RuntimeError("External non-database services require retained-install checks")
    environment = services["api"]["environment"]
    if environment.get("KEY_PROVIDER_TYPE") != "openbao-local":
        raise RuntimeError("External key-provider state requires retained-install checks")
    if any(environment.get(key) for key in ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET_NAME")):
        raise RuntimeError("External file storage requires retained-install checks")
    for key, host in (("CACHE_URL", f"{project}_redis"), ("BROKER_URL", f"{project}_redis_broker")):
        if urlsplit(environment.get(key, "")).hostname != host:
            raise RuntimeError("External Redis state requires retained-install checks")
    for key, expected in (
        ("SOCKET_INTERNAL_URL", f"http://{project}_socket_phoenix:5690"),
        ("SOCKET_EDITOR_INTERNAL_URL", f"http://{project}_server:8010"),
    ):
        if environment.get(key) != expected:
            raise RuntimeError("External editor state requires retained-install checks")
    brokers = ",".join(f"{project}_kafka{index}:9092" for index in range(3))
    if environment.get("BROADCAST_TYPE") != "kafka" or any(
        services[name]["environment"].get("BROADCAST_URLS") != brokers for name in ("api", "socket-phoenix")
    ):
        raise RuntimeError("External Kafka state requires retained-install checks")
    if "db" in services:
        # The bundled database has never had storage: its configured local volume
        # does not exist. Existing even-empty volumes cannot select this path.
        mounts = services["db"].get("volumes", [])
        if not any(m.get("type") == "volume" and m["target"] == "/bitnami/postgresql" for m in mounts):
            raise RuntimeError("Bundled PostgreSQL storage identity is unknown")
        for key, database in (("MAIN_DATABASE_URL", "master"), ("READONLY_DATABASE_URL", "readonly")):
            url = urlsplit(environment.get(key, ""))
            if (
                url.scheme not in {"postgresql", "postgres"}
                or url.hostname != f"{project}_db_bouncer"
                or url.path != f"/{database}"
            ):
                raise RuntimeError("Bundled PostgreSQL runtime points at unknown database storage")
    else:
        urls = {environment.get("MAIN_DATABASE_URL"), environment.get("READONLY_DATABASE_URL")}
        for url in urls:
            if not url or not url.startswith(("postgresql://", "postgres://")):
                raise RuntimeError("Effective external PostgreSQL URL is unknown")
            if not empty_external_database(url):
                return False
    return True


def main():
    try:
        args = sys.argv[1:]
        if args[:1] == ["--"]:
            args = args[1:]
        config = json.loads(docker("compose", *args, "config", "--format", "json"))
        if first_install(config, Path.cwd().resolve()):
            print("First installation proven: no retained Docker, database, queue or editor state.")
            return 0
        print("Retained installation detected; all Phoenix cutover checks remain required.")
        return RETAINED
    except RuntimeError as error:
        print(f"First-install proof denied: {error}.", file=sys.stderr)
        return 1
    except Exception:
        # Neither a failed query nor missing permissions/dependencies prove empty
        # data. Keep connection strings and server error details out of the log.
        print(
            "First-install evidence unavailable or unsupported. Refusing bootstrap; restore access to the configured storage or use the retained-install cutover procedure.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
