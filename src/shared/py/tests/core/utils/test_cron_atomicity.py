"""Standalone cron boundary regressions; needs installed crontab and psutil only.

Run: python src/shared/py/tests/core/utils/test_cron_atomicity.py
CRON_UTILS_SOURCE may point to the patched CronTabUtils.py in an isolated image.
Package bootstrap is isolated so database/configuration services are unnecessary.
"""

import importlib.util
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import sleep
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = (
    Path(os.environ["CRON_UTILS_SOURCE"])
    if "CRON_UTILS_SOURCE" in os.environ
    else Path(__file__).resolve().parents[3] / "langboard_shared/core/utils/CronTabUtils.py"
)
for name in ("_cron_regression", "_cron_regression.core", "_cron_regression.core.utils"):
    package = ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package
configuration = ModuleType("_cron_regression.Env")
configuration.Env = SimpleNamespace(ENVIRONMENT="development", CRON_TAB_FILE=Path("unused"))
sys.modules[configuration.__name__] = configuration
types = ModuleType("_cron_regression.core.types")
types.SafeDateTime = datetime
sys.modules[types.__name__] = types
spec = importlib.util.spec_from_file_location("_cron_regression.core.utils.CronTabUtils", SOURCE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def writer(path, operation, comment, entered=None, release=None):
    utils = module.CronTabUtils(path)

    @utils.mutation
    def mutate():
        cron = utils.get_cron()
        if entered is not None:
            entered.set()
        if release is not None and not release.wait(10):
            raise TimeoutError("writer gate")
        if operation == "add":
            utils.create_job(cron, "* * * * *", "echo test", comment)
        else:
            utils.remove_job(cron, comment)
        utils.save_cron(cron)

    mutate()


class CronAtomicity(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "cron.tab"
        self.utils = module.CronTabUtils(self.path)
        module.Env.ENVIRONMENT = "development"

    def comments(self):
        return {job.comment for job in self.utils.get_cron()}

    def test_process_writers_preserve_add_and_delete(self):
        context = multiprocessing.get_context("spawn")
        for second, expected in (("add", {"old", "first", "second"}), ("delete", {"first"})):
            with self.subTest(second=second):
                self.path.unlink(missing_ok=True)
                writer(self.path, "add", "old")
                first_entered, release, second_entered = (context.Event() for _ in range(3))
                first = context.Process(target=writer, args=(self.path, "add", "first", first_entered, release))
                other = context.Process(target=writer, args=(
                    self.path, second, "second" if second == "add" else "old", second_entered,
                ))
                first.start()
                try:
                    self.assertTrue(first_entered.wait(10))
                    other.start()
                    self.assertFalse(second_entered.wait(0.3), "second process bypassed file lock")
                    release.set()
                    first.join(10)
                    other.join(10)
                    self.assertEqual((first.exitcode, other.exitcode), (0, 0))
                    self.assertEqual(self.comments(), expected)
                finally:
                    release.set()
                    for process in (first, other):
                        if process.pid:
                            if process.is_alive():
                                process.terminate()
                            process.join(10)

    def test_thread_writers_preserve_unrelated_jobs(self):
        def add(comment):
            @self.utils.mutation
            def mutate():
                cron = self.utils.get_cron()
                sleep(0.01)
                self.utils.create_job(cron, "* * * * *", "echo test", comment)
                self.utils.save_cron(cron)
            mutate()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(add, [str(index) for index in range(8)]))
        self.assertEqual(self.comments(), {str(index) for index in range(8)})

    def test_failed_replace_preserves_committed_file_and_cleans_temporary(self):
        writer(self.path, "add", "old")
        committed = self.path.read_bytes()
        with patch.object(module, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                writer(self.path, "add", "new")
        self.assertEqual(self.path.read_bytes(), committed)
        self.assertEqual(list(self.path.parent.glob(".cron.tab.*")), [])
        writer(self.path, "add", "after_failure")
        self.assertEqual(self.comments(), {"old", "after_failure"})

    def test_atomic_write_replaces_inode_and_preserves_permissions(self):
        writer(self.path, "add", "old")
        self.path.chmod(0o640)
        with self.path.open() as previous:
            old_contents = previous.read()
            writer(self.path, "add", "new")
            previous.seek(0)
            self.assertEqual(previous.read(), old_contents)
            self.assertNotEqual(os.fstat(previous.fileno()).st_ino, self.path.stat().st_ino)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.comments(), {"old", "new"})

    def test_static_descriptor_and_reentrant_alias_lock(self):
        utils = self.utils
        alias = module.CronTabUtils(self.path.parent / "." / self.path.name)

        class Consumer:
            @staticmethod
            @utils.static_mutation
            def update(comment):
                writer(alias.file_path, "add", comment)
                return comment

        self.assertIsInstance(Consumer.__dict__["update"], staticmethod)
        self.assertEqual(Consumer.update("class"), "class")
        self.assertEqual(Consumer().update("instance"), "instance")
        self.assertEqual(self.comments(), {"class", "instance"})

    def test_missing_process_lock_fails_closed(self):
        with patch.object(module, "_fcntl", None), patch.object(module, "_msvcrt", None):
            with self.assertRaisesRegex(RuntimeError, "Cross-process cron file locking is unavailable"):
                self.utils.save_cron(self.utils.get_cron())

    def test_nonroot_scheduler_is_reused_and_restarted(self):
        running = SimpleNamespace(poll=lambda: None)
        stopped = SimpleNamespace(poll=lambda: 1)
        module.Env.ENVIRONMENT = "production"
        with (
            patch.object(module.os, "geteuid", return_value=10001, create=True),
            patch.object(module, "subprocess_run", side_effect=AssertionError("system cron invoked")),
            patch.object(module, "Popen", return_value=running) as spawn,
            patch.object(module, "_scheduler_process", None),
            patch.object(module, "environ", {"CURRENT_CRON_ENV": "fresh"}),
        ):
            writer(self.path, "add", "first")
            self.utils.reload_cron()
            self.assertEqual(spawn.call_count, 1)
            self.assertEqual(spawn.call_args.args[0][-1], str(self.path))
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            module._scheduler_process = stopped
            self.utils.reload_cron()
            self.assertEqual(spawn.call_count, 2)
            self.assertIn("CURRENT_CRON_ENV=fresh", self.path.read_text())

    def test_root_installs_and_preserves_running_daemon(self):
        module.Env.ENVIRONMENT = "production"
        with (
            patch.object(module.os, "geteuid", return_value=0, create=True),
            patch.object(module, "environ", {}),
            patch.object(module, "Popen", side_effect=AssertionError("fallback invoked")),
            patch.object(module, "process_iter", return_value=[]) as processes,
            patch.object(module, "subprocess_run") as execute,
        ):
            writer(self.path, "add", "root")
            self.assertEqual([call.args[0] for call in execute.call_args_list], [["crontab", str(self.path)], ["cron"]])
            execute.reset_mock()
            processes.return_value = [SimpleNamespace(info={"name": "cron"})]
            self.utils.reload_cron()
            execute.assert_called_once_with(["crontab", str(self.path)], check=True)


if __name__ == "__main__":
    unittest.main()
