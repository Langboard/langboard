"""Exercise warm recovery through the real scheduler without a broker or database."""

import fcntl
import importlib
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


scheduler = importlib.import_module("langboard_shared.core.utils.CronScheduler")


class DocumentCronReuseTest(unittest.TestCase):
    def test_two_ticks_reuse_recovery_and_keep_explicit_environment(self):
        for kind, (setting, override) in product(
            ("document", "internal", "notification", "ollama"), (("false", False), ("true", False), ("false", True))
        ):
            with (
                self.subTest(kind=kind, setting=setting, override=override),
                tempfile.TemporaryDirectory() as directory,
                ExitStack() as stack,
            ):
                root = Path(directory)
                path = root / "cron.tab"
                prefix = "CRON_REUSE_SCOPE=cron\n" if override else ""
                command = (
                    {
                        "document": scheduler.DOCUMENT_RECOVERY_COMMAND,
                        "internal": scheduler.INTERNAL_BOT_RECOVERY_COMMAND,
                        "notification": scheduler.NOTIFICATION_RECOVERY_COMMAND,
                        "ollama": scheduler.OLLAMA_RECOVERY_COMMAND,
                    }[kind]
                )
                path.write_text(prefix + f"* * * * * /bin/bash {command}\n")
                previous = Path.cwd()
                stack.callback(os.chdir, previous)
                os.chdir(root)
                stack.enter_context(patch.object(scheduler, "DOCUMENT_RECOVERY_DIRECTORY", root))
                stack.enter_context(patch.object(scheduler, "DOCUMENT_RECOVERY_LOCK", root / "document.lock"))
                stack.enter_context(patch.object(scheduler, "INTERNAL_BOT_RECOVERY_LOCK", root / "internal.lock"))
                stack.enter_context(patch.object(scheduler, "NOTIFICATION_RECOVERY_LOCK", root / "notification.lock"))
                stack.enter_context(patch.object(scheduler, "OLLAMA_RECOVERY_LOCK", root / "ollama.lock"))
                stack.enter_context(
                    patch.dict(os.environ, SENTRY_AUTO_ENABLING_INTEGRATIONS=setting, CRON_REUSE_SCOPE="parent")
                )
                recover = Mock()
                stack.enter_context(
                    patch.dict(
                        sys.modules,
                        {
                            "langboard_shared.tasks.docling.DocumentRecovery": SimpleNamespace(
                                recover_documents=recover
                            ),
                            "langboard_shared.tasks.notifications.NotificationWebFanoutTask": SimpleNamespace(
                                recover_notifications=recover
                            ),
                            "langboard.commands.RunOllamaModelPullRecoveryCommand": SimpleNamespace(
                                RunOllamaModelPullRecoveryCommand=lambda: SimpleNamespace(execute=recover),
                                RunOllamaModelPullRecoveryCommandOptions=lambda: None,
                            ),
                            "langboard.commands.RunInternalBotRunRecoveryCommand": SimpleNamespace(
                                RunInternalBotRunRecoveryCommand=lambda: SimpleNamespace(execute=recover),
                                RunInternalBotRunRecoveryCommandOptions=lambda: None,
                            ),
                        },
                    )
                )
                shell = stack.enter_context(patch.object(importlib.import_module("crontab").CronItem, "run"))
                runner = scheduler.CronScheduler(path)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    for minute in (1, 2):
                        runner.tick(pool, runner.started_at + timedelta(minutes=minute))
                        for future in runner.running.values():
                            future.result(timeout=5)
                reuse = setting == "false" and not override
                self.assertEqual(recover.call_count, 2 if reuse else 0)
                self.assertEqual(shell.call_count, 0 if reuse else 2)

    def test_cli_lock_excludes_work_and_failure_releases_it(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "document.lock"
            stack.enter_context(patch.object(scheduler, "DOCUMENT_RECOVERY_LOCK", path))
            recover = Mock(side_effect=[RuntimeError("recovery failed"), None])
            stack.enter_context(
                patch.dict(
                    sys.modules,
                    {
                        "langboard_shared.tasks.docling.DocumentRecovery": SimpleNamespace(recover_documents=recover),
                    },
                )
            )
            with path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                scheduler.run_document_recovery()
                recover.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                scheduler.run_document_recovery()
            scheduler.run_document_recovery()
            self.assertEqual(recover.call_count, 2)

    def test_internal_cli_lock_excludes_work_and_failure_releases_it(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "internal.lock"
            stack.enter_context(patch.object(scheduler, "INTERNAL_BOT_RECOVERY_LOCK", path))
            recover = Mock(side_effect=[RuntimeError("recovery failed"), None])
            stack.enter_context(patch.dict(sys.modules, {
                "langboard.commands.RunInternalBotRunRecoveryCommand": SimpleNamespace(
                    RunInternalBotRunRecoveryCommand=lambda: SimpleNamespace(execute=recover),
                    RunInternalBotRunRecoveryCommandOptions=lambda: None,
                ),
            }))
            with path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                scheduler.run_internal_bot_recovery()
                recover.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                scheduler.run_internal_bot_recovery()
            scheduler.run_internal_bot_recovery()
            self.assertEqual(recover.call_count, 2)

    def test_notification_and_ollama_locks_exclude_and_release(self):
        for kind, module, fake in (
            ("notification", "langboard_shared.tasks.notifications.NotificationWebFanoutTask", lambda recover: SimpleNamespace(recover_notifications=recover)),
            ("ollama", "langboard.commands.RunOllamaModelPullRecoveryCommand", lambda recover: SimpleNamespace(
                RunOllamaModelPullRecoveryCommand=lambda: SimpleNamespace(execute=recover),
                RunOllamaModelPullRecoveryCommandOptions=lambda: None,
            )),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                path = Path(directory) / "recovery.lock"
                stack.enter_context(patch.object(scheduler, f"{kind.upper()}_RECOVERY_LOCK", path))
                recover = Mock(side_effect=[RuntimeError("recovery failed"), None])
                stack.enter_context(patch.dict(sys.modules, {module: fake(recover)}))
                execute = getattr(scheduler, f"run_{kind}_recovery")
                with path.open("a+") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    execute()
                    recover.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                    execute()
                execute()
                self.assertEqual(recover.call_count, 2)

    def test_document_recovery_keeps_existing_order(self):
        recovery = importlib.import_module("langboard_shared.tasks.docling.DocumentRecovery")
        calls = []
        names = ("recover_indexes", "recover_cleanup", "recover_keyword", "recover_card_location_embeddings")
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(recovery, name, side_effect=lambda name=name: calls.append(name)))
            recovery.recover_documents()
        self.assertEqual(calls, list(names))


if __name__ == "__main__":
    unittest.main()
