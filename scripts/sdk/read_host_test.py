"""Keep unknown hosts and mismatched packages distinct from verified metadata."""

import unittest

from read_host import compare


class HostReadbackTests(unittest.TestCase):
    def test_identity_requires_version_and_original_module_digest(self):
        provenance = {"packages": {"@langboard/app-panel": "0.2.7"},
                      "app_panel_module_sha256": "a" * 64}
        host = {"name": "@langboard/app-panel", "version": "0.2.7",
                "module_sha256": "a" * 64}
        self.assertEqual(compare({"version": 1}, provenance), "unknown")
        self.assertEqual(compare({"host_panel_sdk": host}, provenance), "match")
        for change in ({"version": "0.2.6"}, {"module_sha256": "b" * 64}):
            with self.subTest(change=change):
                self.assertEqual(compare({"host_panel_sdk": host | change}, provenance), "different")
        for invalid in ([], {}, host | {"name": "other"}, host | {"version": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                compare({"host_panel_sdk": invalid}, provenance)


if __name__ == "__main__":
    unittest.main()
