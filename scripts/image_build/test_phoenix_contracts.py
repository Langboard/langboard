"""Control-flow doubles are not native PostgreSQL acceptance evidence."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

try:
    from . import run_phoenix_contracts as runner
except ImportError:
    import run_phoenix_contracts as runner


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self):
        self.databases = {}
        self.events = []
        self.fail_second = False
        self.fail_drop = False
        self.owner = 10
        self.privilege = True
        self.urls = []
        self.closed = []
        self.branch_row = None
        self.unproven = False

    def connect(self, *args, **kwargs):
        self.events.append("connect")
        self.urls.append(args[0])
        self.current = args[0].rsplit("/", 1)[1]
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed.append(self.current)

    def execute(self, query, parameters=None):
        self.events.append(query)
        address = "127.0.0.1" if "host(inet_server_addr())" in query else "127.0.0.1/32"
        if "WHERE datname = current_database()" in query:
            return Result(self.branch_row or (
                self.current, "postgres", "postgres", address, 5432,
                *self.databases[self.current]))
        if query.startswith("SELECT current_database"):
            return Result(("validation", "postgres", "postgres", address, 5432,
                           self.owner, self.privilege, False))
        if query.startswith("SELECT oid"):
            if self.unproven and parameters[0] in self.databases:
                return Result(None)
            return Result(self.databases.get(parameters[0]))
        name = query.split('"')[1]
        if query.startswith("CREATE"):
            if self.fail_second and name == runner.DATABASES[1]:
                raise RuntimeError("second creation rejected")
            if name in self.databases:
                raise RuntimeError("already exists")
            self.databases[name] = (100 + len(self.databases), self.owner)
        elif query.startswith("DROP"):
            if self.fail_drop:
                raise RuntimeError("drop rejected")
            del self.databases[name]
        else:
            raise AssertionError(query)
        return Result(None)


def environment():
    return {"DATABASE_URL": runner.ADMIN, **{name: runner.SQLALCHEMY for name in runner.REQUIRED}}


class DatabaseOwnershipTests(unittest.TestCase):
    def test_native_inet_text_mask_does_not_reject_loopback(self):
        fake = FakeDatabase()
        owned = runner.Databases(fake.connect)
        owned.create()
        self.assertEqual(set(owned.owned), set(runner.DATABASES))
        self.assertEqual(owned.cleanup(), [])
        self.assertEqual(fake.databases, {})

    def test_branch_connections_verify_exact_urls_and_close(self):
        fake = FakeDatabase()
        owned = runner.Databases(fake.connect)
        owned.create()
        self.assertEqual(fake.urls, [runner.ADMIN] + [
            runner.branch_environment(environment(), name)["DATABASE_URL"]
            for name in runner.DATABASES])
        self.assertEqual(fake.closed, ["validation", *runner.DATABASES])
        self.assertEqual(len({row[0] for row in owned.owned.values()}), 2)
        self.assertEqual(owned.cleanup(), [])

    def test_branch_connection_wrong_database_oid_or_owner_rejected(self):
        expected = (runner.DATABASES[0], "postgres", "postgres", "127.0.0.1", 5432, 100, 10)
        for index, replacement in ((0, "validation"), (1, "other"), (2, "other"),
                                   (3, "192.0.2.1"), (4, 6543), (5, 999), (6, 20)):
            with self.subTest(field=index):
                fake = FakeDatabase()
                row = list(expected)
                row[index] = replacement
                fake.branch_row = tuple(row)
                owned = runner.Databases(fake.connect)
                with self.assertRaisesRegex(RuntimeError, "Branch connection identity rejected"):
                    owned.create()
                # The correct admin catalog must not substitute for branch proof.
                self.assertEqual(fake.databases, {runner.DATABASES[0]: (100, 10),
                                                 runner.DATABASES[1]: (101, 10)})
                self.assertEqual(fake.closed, ["validation", runner.DATABASES[0]])
                self.assertEqual(owned.cleanup(), [])

    def test_unproven_successful_creation_reports_unowned_residual(self):
        fake = FakeDatabase()
        fake.unproven = True
        owned = runner.Databases(fake.connect)
        with self.assertRaisesRegex(RuntimeError, "ownership could not be proven"):
            owned.create()
        self.assertFalse(owned.owned)
        errors = owned.cleanup()
        self.assertEqual(len(errors), 1)
        self.assertIn("unowned residual", errors[0])
        self.assertIn(runner.DATABASES[0], errors[0])
        self.assertFalse(any(event.startswith("DROP") for event in fake.events))
        self.assertIn(runner.DATABASES[0], fake.databases)

    def test_strict_environment_and_branch_remapping(self):
        source = runner.checked_environment(environment())
        for database in runner.DATABASES:
            branch = runner.branch_environment(source, database)
            self.assertTrue(all(branch[name].endswith("/" + database) for name in runner.REQUIRED))
            self.assertEqual(branch["PGDATABASE"], database)
        self.assertIn("LANGBOARD_TEST_DATABASE_URL", runner.branch_environment(source, runner.DATABASES[0]))
        self.assertNotIn("LANGBOARD_TEST_DATABASE_URL", runner.branch_environment(source, runner.DATABASES[1]))
        explicit = {**source, "LANGBOARD_TEST_DATABASE_URL": runner.SQLALCHEMY}
        self.assertTrue(runner.branch_environment(explicit, runner.DATABASES[1])["LANGBOARD_TEST_DATABASE_URL"].endswith("/" + runner.DATABASES[1]))
        for key, value in (("DATABASE_URL", runner.ADMIN + "?options=x"),
                           ("DATABASE_URL", runner.ADMIN.replace("127.0.0.1", "localhost")),
                           ("PGUSER", "other"), ("PGOPTIONS", "-c role=other"),
                           ("LANGBOARD_PRIVATE_TEST_DATABASE_URL", runner.ADMIN),
                           ("LANGBOARD_TEST_DATABASE_URL", "postgresql://foreign/db")):
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                runner.checked_environment({**source, key: value})

    def test_partial_creation_cleans_only_proven_database(self):
        fake = FakeDatabase()
        fake.fail_second = True
        owned = runner.Databases(fake.connect)
        with self.assertRaises(RuntimeError):
            owned.create()
        self.assertEqual(list(owned.owned), [runner.DATABASES[0]])
        self.assertEqual(owned.cleanup(), [])
        self.assertEqual(fake.databases, {})
        self.assertEqual(fake.events.count("connect"), 3)

    def test_stale_database_rejected_without_drop_or_create(self):
        for name in runner.DATABASES:
            fake = FakeDatabase()
            fake.databases[name] = (999, 20)
            owned = runner.Databases(fake.connect)
            with self.assertRaises(RuntimeError):
                owned.create()
            self.assertEqual(owned.cleanup(), [])
            self.assertFalse(any(event.startswith(("CREATE", "DROP")) for event in fake.events))

    def test_actual_privilege_required(self):
        fake = FakeDatabase()
        fake.privilege = False
        with self.assertRaises(RuntimeError):
            runner.Databases(fake.connect).create()
        self.assertFalse(fake.databases)

    def test_foreign_replacement_is_not_dropped(self):
        fake = FakeDatabase()
        owned = runner.Databases(fake.connect)
        owned.create()
        fake.databases[runner.DATABASES[0]] = (999, 20)
        self.assertTrue(owned.cleanup())
        self.assertEqual(fake.databases, {runner.DATABASES[0]: (999, 20)})

    def test_cleanup_failure_is_explicit(self):
        fake = FakeDatabase()
        owned = runner.Databases(fake.connect)
        owned.create()
        fake.fail_drop = True
        self.assertEqual(len(owned.cleanup()), 2)


class BranchProcessTests(unittest.TestCase):
    def test_interrupt_preserves_already_completed_branch_failure(self):
        fake = FakeDatabase()
        failed, pending = Mock(), Mock()
        failed.poll.return_value = 17
        pending.poll.return_value = None

        def interrupted_join(commands, environments, processes):
            if len(commands) == 1:
                return 0
            processes.extend([failed, pending])
            raise InterruptedError("Producer deadline reached")

        with patch.dict(os.environ, environment(), clear=True), \
                patch.object(runner, "subreaper"), \
                patch.object(runner, "joined", side_effect=interrupted_join), \
                patch.object(runner, "stop_children") as stop:
            self.assertEqual(runner.run("prefix.sh", "results", fake.connect), 17)
        stop.assert_called_once_with([failed, pending])
        self.assertEqual(fake.databases, {})

    def test_real_children_start_together_and_both_join(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = """import pathlib, sys, time
root=pathlib.Path(sys.argv[1]); name=sys.argv[2]
(root/name).write_text('started')
deadline=time.monotonic()+3
while not (root/sys.argv[3]).exists():
    if time.monotonic()>deadline: raise SystemExit(99)
    time.sleep(.01)
time.sleep(.1)
(root/(name+'-done')).write_text('joined')
raise SystemExit(int(sys.argv[4]))
"""
            processes = []
            status = runner.joined(
                [[sys.executable, "-c", source, directory, "card", "tail", "17"],
                 [sys.executable, "-c", source, directory, "tail", "card", "0"]],
                [os.environ.copy(), os.environ.copy()], processes)
            self.assertEqual(status, 17)
            self.assertTrue(all(process.poll() is not None for process in processes))
            self.assertTrue((root / "tail-done").is_file())
            self.assertTrue((root / "card-done").is_file())

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires native Linux subreaper")
    def test_term_reaps_detached_descendants_before_database_cleanup(self):
        self.run_supervisor_probe(terminate=True)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires native Linux subreaper")
    def test_branch_failure_preserved_when_cleanup_also_fails(self):
        self.run_supervisor_probe(terminate=False)

    def run_supervisor_probe(self, *, terminate):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prefix.sh").write_text("exit 0\n")
            (root / "descendant.py").write_text("import os,time,pathlib,sys\nos.setsid()\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(60)\n")
            code = '''import os,sys,pathlib,shlex
sys.path.insert(0,sys.argv[1])
import run_phoenix_contracts as r
from test_phoenix_contracts import FakeDatabase,environment
root=pathlib.Path(sys.argv[2]); terminate=sys.argv[3]=='yes'
loader_path=os.environ.get('LD_LIBRARY_PATH','')
os.environ.clear(); os.environ.update(environment()); os.environ['PATH']='/usr/bin:/bin'; os.environ['LD_LIBRARY_PATH']=loader_path
fake=FakeDatabase(); fake.fail_drop=not terminate
original=fake.execute
def execute(query,parameters=None):
    if query.startswith('DROP'):
        pidfile=root/'descendant.pid'
        if pidfile.exists():
            try: os.kill(int(pidfile.read_text()),0)
            except ProcessLookupError: pass
            else: raise AssertionError('drop before descendant settled')
        (root/'cleanup').write_text('settled')
    return original(query,parameters)
fake.execute=execute
if terminate:
    r.CARD=shlex.quote(sys.executable)+' '+shlex.quote(str(root/'descendant.py'))+' '+shlex.quote(str(root/'descendant.pid'))+' & wait'
    r.TAIL='sleep 60'
else:
    r.CARD='exit 17'; r.TAIL='sleep .1; exit 0'
raise SystemExit(r.run(str(root/'prefix.sh'),str(root),fake.connect))
'''
            process = subprocess.Popen([sys.executable, "-c", code, str(Path(runner.__file__).parent),
                                        directory, "yes" if terminate else "no"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                if terminate:
                    deadline = time.monotonic() + 5
                    while not (root / "descendant.pid").exists() and process.poll() is None:
                        if time.monotonic() >= deadline:
                            self.fail("descendant did not start")
                        time.sleep(.01)
                    process.send_signal(signal.SIGTERM)
                _, stderr = process.communicate(timeout=9)
                self.assertEqual(process.returncode, 124 if terminate else 17, stderr)
                self.assertTrue((root / "cleanup").is_file(), stderr)
                if terminate:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int((root / "descendant.pid").read_text()), 0)
                else:
                    self.assertIn("Phoenix cleanup failed", stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
