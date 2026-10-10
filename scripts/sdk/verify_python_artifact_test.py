"""Reject poisoned wheels and drift between project, provenance and lock."""

import json
import shutil
import tempfile
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
        shutil.copy2(source / "src/sdk/vendor/provenance.json", self.vendor)
        for path in (source / "src/sdk/vendor").glob("*.whl"):
            shutil.copy2(path, self.vendor)
        self.artifact = next(self.vendor.glob("*.whl"))

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
        path.write_text(path.read_text().replace('version = "0.2.13"', 'version = "0.0.0"'))
        with self.assertRaisesRegex(ValueError, "lock identity mismatch"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()
