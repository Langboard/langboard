"""Keep the standalone gate and split BuildPlane gate equivalent."""

import ast
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PhoenixValidation(unittest.TestCase):
    def test_static_export_retains_collector_result_root(self):
        # The central collector reads export/result, not the export root itself.
        dockerfile = (ROOT / "docker/phoenix-validation.Dockerfile").read_text()
        export = dockerfile.split("FROM scratch AS validation-results\n", 1)[1].split("\nFROM ", 1)[0]
        copies = [shlex.split(line) for line in export.splitlines() if line.startswith("COPY ")]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0][2].rstrip("/"), "/result")
        self.assertEqual(copies[0][3].rstrip("/"), "/result")

    def test_runtime_images_include_every_registered_cron_entrypoint(self):
        runner = ast.parse((ROOT / "src/api/langboard/ServerRunner.py").read_text())
        commands = {
            node.value.removeprefix("/app/")
            for node in ast.walk(runner)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("/app/scripts/")
            and node.value.endswith(".sh")
        }
        self.assertTrue(commands)
        for filename in ("Dockerfile", "docker/api-canary-development.Dockerfile"):
            # Later validation stages copy all scripts and cannot prove runtime packaging.
            text = (ROOT / filename).read_text().split("\nFROM base AS with-aws", 1)[0]
            copied = set()
            for line in text.splitlines():
                if not line.startswith("COPY "):
                    continue
                parts = shlex.split(line)
                if parts and parts[0] == "COPY" and parts[-1] == "./scripts/":
                    for pattern in parts[1:-1]:
                        if not pattern.startswith("--"):
                            copied.update(
                                str(path.relative_to(ROOT)) for path in ROOT.glob(pattern.removeprefix("./"))
                            )
            with self.subTest(image=filename):
                self.assertFalse(
                    commands - copied,
                    f"Registered cron entrypoints absent from {filename}: {sorted(commands - copied)}",
                )

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
