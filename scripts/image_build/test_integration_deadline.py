"""Native result preservation when the integration producer stops or times out."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check-phoenix-validation.sh"


class IntegrationDeadlineTests(unittest.TestCase):
    def run_producer(self, *, expire=False, fail=False, native_signal=False, writer_fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            scripts = {
                "timeout": """#!/bin/sh
printf '%s\\n' "$@" > "$TIMEOUT_TRACE"
if [ "$TIMEOUT_MODE" = expire ]; then exit 124; fi
if [ "$TIMEOUT_MODE" = signal ]; then
  shift 3
  exec /usr/bin/timeout --signal=TERM --kill-after=1s 0.2s "$@"
fi
shift 3
exec "$@"
""",
                "uv": """#!/bin/sh
if [ "$1" = run ] && [ "$2" = --no-sync ] && [ "$3" = python ] && [ "$4" = - ]; then
  if [ "$FAIL_RESULT_WRITER" = yes ]; then exit 31; fi
  shift 3
  exec "$TEST_PYTHON" "$@"
fi
case "$*" in
  *scripts/sdk/verify_python_artifact.py*)
    if [ "$FAIL_ARTIFACT" = yes ]; then exit 17; fi
    if [ "$FAIL_ARTIFACT" = sleep ]; then sleep 5; fi ;;
esac
exit 0
""",
                "node": "#!/bin/sh\nexit 0\n",
                "make": "#!/bin/sh\nexit 0\n",
            }
            for name, content in scripts.items():
                path = commands / name
                path.write_text(content)
                path.chmod(0o755)
            for name in (
                "conftest.py",
                "scripts/sdk/verify_python_artifact_test.py",
                "src/shared/py/langboard_shared/domain/services/factory/ProjectColumnService_test.py",
                "src/shared/py/langboard_shared/core/logger/LogFileHandler_test.py",
                "src/shared/py/langboard_shared/core/logger/Telemetry_test.py",
                "src/shared/py/langboard_shared/core/routing/AppExceptionHandlingRoute_test.py",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Isolated command fixture.\n")
            for name in ("check-card-workspace.sh", "check-app-contracts.sh"):
                (root / "scripts" / name).write_text("exit 0\n")
            results = root / "results"
            results.mkdir()
            if writer_fail:
                (results / "result.json").write_text('{"schemaVersion":1,"exitCode":0,"reports":{}}\n')
            # Previously completed reports must survive a later producer timeout.
            for name in ("graph-contracts.xml", "card-workspace-1.xml"):
                (results / name).write_text("<testsuites/>\n")
            environment = {
                "PATH": str(commands) + os.pathsep + os.environ.get("PATH", ""),
                "TEST_PYTHON": sys.executable,
                "TIMEOUT_TRACE": str(root / "timeout-arguments"),
                "TIMEOUT_MODE": "signal" if native_signal else ("expire" if expire else "forward"),
                "FAIL_ARTIFACT": "sleep" if native_signal else ("yes" if fail else "no"),
                "FAIL_RESULT_WRITER": "yes" if writer_fail else "no",
            }
            for name in (
                "LANGBOARD_FILE_TEST_DATABASE_URL", "LANGBOARD_OUTBOX_TEST_DATABASE_URL",
                "LANGBOARD_EFFECT_TEST_DATABASE", "LANGBOARD_TEMPLATE_TEST_DATABASE_URL",
            ):
                environment[name] = "isolated-command-fixture"
            process = subprocess.run(
                ["bash", str(SCRIPT), str(results)], cwd=root, env=environment,
                capture_output=True, text=True, timeout=10,
            )
            if writer_fail:
                self.assertFalse((results / "result.json").exists())
                return process.returncode, None, (root / "timeout-arguments").read_text().splitlines()
            self.assertTrue((results / "result.json").is_file(), process.stderr)
            result = json.loads((results / "result.json").read_text())
            for names in result["reports"].values():
                for name in names:
                    self.assertTrue((results / name).is_file(), name)
            return process.returncode, result, (
                root / "timeout-arguments"
            ).read_text().splitlines()

    def test_timeout_exports_failure_and_completed_reports(self):
        status, result, arguments = self.run_producer(expire=True)
        self.assertEqual((status, result["exitCode"]), (124, 124))
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["reports"]["junit"], ["graph-contracts.xml", "card-workspace-1.xml"])
        self.assertEqual(result["reports"]["artifacts"], ["artifacts/phoenix-integration.log"])
        self.assertEqual(arguments[:3], ["--signal=TERM", "--kill-after=10s", "1740s"])

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/bin/timeout").is_file(), "requires native GNU timeout")
    def test_native_signal_stops_child_and_exports_timeout(self):
        status, result, _ = self.run_producer(native_signal=True)
        self.assertEqual((status, result["exitCode"]), (124, 124))

    def test_writer_failure_does_not_leave_prior_success(self):
        status, result, _ = self.run_producer(writer_fail=True)
        self.assertNotEqual(status, 0)
        self.assertIsNone(result)

    def test_completed_command_preserves_success(self):
        status, result, _ = self.run_producer()
        self.assertEqual((status, result["exitCode"]), (0, 0))

    def test_failed_command_keeps_original_failure_status(self):
        status, result, _ = self.run_producer(fail=True)
        self.assertEqual((status, result["exitCode"]), (17, 17))


if __name__ == "__main__":
    unittest.main()
