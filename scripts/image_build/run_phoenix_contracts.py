"""Bounded native Phoenix branches; only owned disposable databases are removed."""

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ADMIN = "postgresql://postgres@127.0.0.1:5432/validation"
SQLALCHEMY = ADMIN.replace("postgresql:", "postgresql+psycopg:")
DATABASES = ("langboard_test", "langboard_wiki_test")
REQUIRED = ("LANGBOARD_FILE_TEST_DATABASE_URL", "LANGBOARD_OUTBOX_TEST_DATABASE_URL",
            "LANGBOARD_EFFECT_TEST_DATABASE", "LANGBOARD_TEMPLATE_TEST_DATABASE_URL")
OPTIONAL = ("LANGBOARD_TEST_DATABASE_URL", "LANGBOARD_COUNT_TEST_DATABASE_URL",
            "LANGBOARD_MIRROR_TEST_DATABASE_URL", "LANGBOARD_PROJECTION_TEST_DATABASE_URL",
            "LANGBOARD_SUMMARY_TEST_DATABASE_URL", "LANGBOARD_WIKI_TEST_DATABASE_URL",
            "SCHEDULE_WORKLOAD_TEST_URL")

CARD = 'result_dir=$1; bash scripts/check-card-workspace.sh "$result_dir"'
TAIL = 'result_dir=$1; bash scripts/check-app-contracts.sh "$result_dir/app-contracts.xml" \\\n    && PYTHONPATH=. uv run pytest -p no:tach -q --junitxml="$result_dir/compactor-contracts.xml" src/api/tests/model_providers/test_compactor_profile_provisioning.py src/api/tests/model_providers/test_compaction_workflow_history.py src/api/tests/model_providers/test_compactor_board_authority.py src/api/tests/model_providers/test_comment_compaction_snapshot.py src/api/tests/model_providers/test_automatic_archive_command.py src/api/tests/model_providers/test_comment_compaction_command.py src/api/tests/model_providers/test_compaction_semantic_index.py src/api/tests/model_providers/test_comment_compaction_binding.py src/api/tests/model_providers/test_comment_compaction_store.py src/api/tests/migrations/test_compactor_board_grant_migration.py src/api/tests/migrations/test_compactor_principal_migration.py src/api/tests/migrations/test_workflow_compaction_schedule_migration.py src/api/tests/migrations/test_workflow_stage_residency_migration.py \\\n    && PYTHONPATH=. uv run pytest -p no:tach -q --junitxml="$result_dir/workflow-contracts.xml" src/api/tests/workflow_stages src/api/tests/card_workspace/test_queries.py src/api/tests/card_workspace/test_native_adapter.py src/api/tests/mcp_integration/test_user_mcp.py src/shared/py/langboard_shared/domain/services/factory/ProjectColumnService_test.py src/api/tests/webhooks/test_webhook_contract.py src/api/tests/webhooks/test_work_event_contract.py src/api/tests/card_visibility/test_notification_visibility.py src/api/tests/card_visibility/test_socket_dispatch_http.py src/api/tests/migrations/test_phoenix_canary_union_postgres.py src/api/tests/core/test_recovery_entrypoints.py src/api/tests/core/test_schema_import_cost.py src/api/tests/core/test_factory_dependency_boundary.py src/shared/py/langboard_shared/core/logger/LogFileHandler_test.py src/shared/py/langboard_shared/core/logger/Telemetry_test.py src/shared/py/langboard_shared/core/routing/AppExceptionHandlingRoute_test.py src/api/tests/project_templates/test_template_authorization_http.py src/api/tests/project_templates/test_native_atomic_creation.py src/api/tests/scim/test_project_role_projection_database.py src/api/tests/project_labels/test_title_label_integration.py'


def checked_environment(environment):
    if environment.get("DATABASE_URL") != ADMIN:
        raise RuntimeError("Exact native PostgreSQL admin URL required")
    for key, value in (("PGHOST", "127.0.0.1"), ("PGPORT", "5432"),
                       ("PGUSER", "postgres"), ("PGDATABASE", "validation")):
        if key in environment and environment[key] != value:
            raise RuntimeError(f"Unsupported native identity: {key}")
    for key in environment:
        if key.startswith("PG") and key not in {"PGHOST", "PGPORT", "PGUSER", "PGDATABASE"}:
            raise RuntimeError(f"Unsupported PostgreSQL override: {key}")
        if key.startswith("LANGBOARD_") and "DATABASE" in key and key not in REQUIRED + OPTIONAL:
            raise RuntimeError(f"Unsupported database override: {key}")
    for key in REQUIRED + OPTIONAL:
        if key in REQUIRED or key in environment:
            if environment.get(key) not in (ADMIN, SQLALCHEMY):
                raise RuntimeError(f"Unsupported database URL: {key}")
    return environment.copy()


def branch_environment(environment, database):
    result = environment.copy()
    for key in REQUIRED + OPTIONAL:
        if key in REQUIRED or key in environment or (key == "LANGBOARD_TEST_DATABASE_URL" and database == DATABASES[0]):
            result[key] = SQLALCHEMY.rsplit("/", 1)[0] + "/" + database
    result["DATABASE_URL"] = ADMIN.rsplit("/", 1)[0] + "/" + database
    result["PGDATABASE"] = database
    return result


class Databases:
    def __init__(self, connect):
        self.connect = connect
        self.owned = {}
        self.created = set()

    def connection(self, database="validation"):
        url = ADMIN.rsplit("/", 1)[0] + "/" + database
        return self.connect(url, autocommit=True, connect_timeout=1,
                            options="-c statement_timeout=500 -c lock_timeout=250")

    def identity(self, db):
        row = db.execute("SELECT current_database(), current_user, session_user, "
                         "host(inet_server_addr()), inet_server_port(), oid, rolcreatedb, rolsuper "
                         "FROM pg_roles WHERE rolname = current_user").fetchone()
        if not row or row[:5] != ("validation", "postgres", "postgres", "127.0.0.1", 5432) or not (row[6] or row[7]):
            raise RuntimeError("Native admin identity or CREATE DATABASE privilege rejected")
        return row[5]

    def lookup(self, db, name):
        return db.execute("SELECT oid, datdba FROM pg_database WHERE datname = %s", (name,)).fetchone()

    def create(self):
        with self.connection() as db:
            owner = self.identity(db)
            if any(self.lookup(db, name) is not None for name in DATABASES):
                raise RuntimeError("Preexisting branch database rejected")
            for name in DATABASES:
                # Fixed identifiers only. Never adopt a preexisting database.
                db.execute(f'CREATE DATABASE "{name}" OWNER postgres')
                self.created.add(name)
                row = self.lookup(db, name)
                if not row or row[1] != owner or row[0] in [item[0] for item in self.owned.values()]:
                    raise RuntimeError("Created database ownership could not be proven")
                self.owned[name] = row
        # Exercise each exact derived branch connection, not only the admin catalog.
        for name in DATABASES:
            with self.connection(name) as db:
                row = db.execute(
                    "SELECT current_database(), current_user, session_user, "
                    "host(inet_server_addr()), inet_server_port(), oid, datdba "
                    "FROM pg_database WHERE datname = current_database()"
                ).fetchone()
                expected = (name, "postgres", "postgres", "127.0.0.1", 5432, *self.owned[name])
                if row != expected:
                    raise RuntimeError(f"Branch connection identity rejected: {name}")

    def cleanup(self):
        errors = [f"{name}: unowned residual; CREATE succeeded but ownership was not proven; not dropped"
                  for name in sorted(self.created - self.owned.keys())]
        for name, identity in self.owned.items():
            try:
                with self.connection() as db:
                    owner = self.identity(db)
                    if self.lookup(db, name) != identity or identity[1] != owner:
                        raise RuntimeError("Owned database identity changed")
                    db.execute(f'DROP DATABASE "{name}"')
                # Independent connection and readback, not a successful DROP receipt.
                with self.connection() as db:
                    self.identity(db)
                    if self.lookup(db, name) is not None:
                        raise RuntimeError("Database absence not proven")
            except TimeoutError:
                raise
            except Exception as error:
                errors.append(f"{name}: {error}")
        return errors


def subreaper():
    if not sys.platform.startswith("linux") or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise RuntimeError("Linux child subreaper required")


def descendants():
    parents = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(entry.name)] = int(fields[1])
            except (FileNotFoundError, ProcessLookupError):
                pass
    owned = {os.getpid()}
    while True:
        found = {pid for pid, parent in parents.items() if parent in owned}
        if found <= owned:
            return owned - {os.getpid()}
        owned |= found


def stop_children(processes):
    # The subreaper retains even setsid/double-fork descendants until they are reaped.
    deadline = time.monotonic() + 2
    while True:
        children = descendants()
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in processes:
            process.poll()
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if not pid:
                    break
            except ChildProcessError:
                break
        if not descendants():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned descendants did not settle")
        time.sleep(0.01)


def joined(commands, environments, processes):
    children = []
    for command, environment in zip(commands, environments):
        process = subprocess.Popen(command, env=environment)
        processes.append(process)
        children.append(process)
    # Join both branches; preserve the first branch failure in stable branch order.
    statuses = [process.wait() for process in children]
    return next((status if status > 0 else 128 - status for status in statuses if status), 0)


def run(script, result_dir, connect):
    subreaper()
    processes = []
    databases = Databases(connect)
    status = 0
    def interrupted(signum, frame):
        raise InterruptedError("Producer deadline reached")
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        environment = checked_environment(os.environ)
        status = joined([["bash", script, result_dir, "--run"]], [environment], processes)
        if status == 0:
            databases.create()
            environments = [branch_environment(environment, name) for name in DATABASES]
            commands = [["bash", "-c", command, "phoenix-branch", result_dir]
                        for command in (CARD, TAIL)]
            status = joined(commands, environments, processes)
    except InterruptedError:
        # Keep a failure already observed before cancellation; otherwise report timeout.
        completed = [process.poll() for process in processes]
        status = next((code for code in completed if code is not None and code > 0), 124)
    except Exception as error:
        print(f"Phoenix integration failed: {error}", file=sys.stderr, flush=True)
        status = status or 1
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        errors = []
        def cleanup_expired(signum, frame):
            raise TimeoutError("Cleanup deadline exhausted")
        old_alarm = signal.signal(signal.SIGALRM, cleanup_expired)
        signal.alarm(7)
        try:
            stop_children(processes)
        except Exception as error:
            errors.append(str(error))
        # Never drop databases while owned processes may still be running.
        if not errors:
            try:
                errors.extend(databases.cleanup())
            except Exception as error:
                errors.append(str(error))
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_alarm)
        if errors:
            print("Phoenix cleanup failed: " + "; ".join(errors), file=sys.stderr, flush=True)
            status = status or 1
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return status


def main():
    import psycopg
    return run(sys.argv[1], sys.argv[2], psycopg.connect)


if __name__ == "__main__":
    raise SystemExit(main())
