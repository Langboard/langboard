"""Reject poisoned wheels and drift between project, provenance and lock."""

import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from verify_python_artifact import verify


class ArtifactVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = Path(__file__).resolve().parents[2]
        self.vendor = self.root / "src/sdk/vendor"
        self.vendor.mkdir(parents=True)
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copy2(source / name, self.root / name)
        for relative in ("src/shared/py", "src/graph"):
            project = self.root / relative
            project.mkdir(parents=True)
            for name in ("pyproject.toml", "uv.lock"):
                shutil.copy2(source / relative / name, project / name)
        shutil.copy2(source / "src/sdk/vendor/provenance.json", self.vendor)
        for path in (source / "src/sdk/vendor").glob("*.whl"):
            shutil.copy2(path, self.vendor)
        config = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.artifact = self.root / config["tool"]["uv"]["sources"]["langboard-sdk"]["path"]

    def test_reviewed_distribution(self):
        self.assertEqual(verify(self.root), self.artifact.resolve())

    def test_poisoned_distribution(self):
        with self.artifact.open("ab") as wheel:
            wheel.write(b"changed")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verify(self.root)

    def test_missing_reviewed_digest(self):
        path = self.vendor / "provenance.json"
        data = json.loads(path.read_text())
        del data["artifacts"][self.artifact.name]
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verify(self.root)

    def test_version_drift(self):
        path = self.vendor / "provenance.json"
        data = json.loads(path.read_text())
        data["packages"]["langboard-sdk"] = "0.0.0"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "package identity mismatch"):
            verify(self.root)

    def test_lock_drift(self):
        path = self.root / "uv.lock"
        path.write_text(
            path.read_text().replace(
                'name = "langboard-sdk"\nversion = "'
                + json.loads((self.vendor / "provenance.json").read_text())["packages"]["langboard-sdk"]
                + '"',
                'name = "langboard-sdk"\nversion = "0.0.0"',
            )
        )
        with self.assertRaisesRegex(ValueError, "lock identity mismatch"):
            verify(self.root)

    def test_shared_lock_digest_drift(self):
        path = self.root / "src/shared/py/uv.lock"
        expected = json.loads((self.vendor / "provenance.json").read_text())["artifacts"][self.artifact.name]
        path.write_text(path.read_text().replace(expected, "0" * 64))
        with self.assertRaisesRegex(ValueError, "lock digest mismatch"):
            verify(self.root)

    def test_shared_project_path_drift(self):
        path = self.root / "src/shared/py/pyproject.toml"
        path.write_text(path.read_text().replace(self.artifact.name, "different.whl"))
        with self.assertRaisesRegex(ValueError, "project artifact mismatch"):
            verify(self.root)


    def test_graph_project_path_drift(self):
        path = self.root / "src/graph/pyproject.toml"
        path.write_text(path.read_text().replace(self.artifact.name, "different.whl"))
        with self.assertRaisesRegex(ValueError, "project artifact mismatch"):
            verify(self.root)

    def test_graph_lock_identity_drift(self):
        path = self.root / "src/graph/uv.lock"
        version = json.loads((self.vendor / "provenance.json").read_text())["packages"]["langboard-sdk"]
        path.write_text(path.read_text().replace(
            f'name = "langboard-sdk"\nversion = "{version}"',
            'name = "langboard-sdk"\nversion = "0.0.0"',
        ))
        with self.assertRaisesRegex(ValueError, "lock identity mismatch"):
            verify(self.root)

    def test_graph_lock_digest_drift(self):
        path = self.root / "src/graph/uv.lock"
        expected = json.loads((self.vendor / "provenance.json").read_text())["artifacts"][self.artifact.name]
        path.write_text(path.read_text().replace(expected, "0" * 64))
        with self.assertRaisesRegex(ValueError, "lock digest mismatch"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()
