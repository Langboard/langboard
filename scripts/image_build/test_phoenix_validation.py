"""Keep the standalone gate and split BuildPlane gate equivalent."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PhoenixValidation(unittest.TestCase):
    def commands(self, *targets):
        return subprocess.check_output(
            ["make", "--no-print-directory", "-n", *targets], cwd=ROOT, text=True
        ).splitlines()

    def test_split_gate_retains_every_standalone_command_once(self):
        combined = self.commands("test_socket_phoenix")
        split = self.commands(
            "check_socket_phoenix_python", "check_socket_phoenix_types", "test_socket_phoenix_integration"
        )
        self.assertEqual(combined, split)
        self.assertEqual(sum("yarn typecheck" in line for line in combined), 1)
        self.assertFalse(any("yarn typecheck" in line for line in self.commands("test_socket_phoenix_integration")))

    def test_static_failure_is_exported_without_becoming_success(self):
        script = (ROOT / "scripts/run-profile-validation.sh").read_text()
        for status in (0, 7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake_make = root / "make"
                fake_make.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\"\nexit {status}\n")
                fake_make.chmod(0o755)
                env = {**os.environ, "PROFILE": "phoenix-static", "PATH": f"{root}:{os.environ['PATH']}"}
                run = subprocess.run(
                    ["bash", "-c", script.replace("/result/", str(root / "result") + "/")],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads((root / "result/result.json").read_text())
                self.assertEqual(result["exitCode"], status)
                self.assertEqual(result["reports"]["artifacts"], ["artifacts/phoenix-static-validation.log"])
                self.assertIn("check_socket_phoenix_python check_socket_phoenix_types", run.stdout)


if __name__ == "__main__":
    unittest.main()
