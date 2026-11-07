"""Keep unknown hosts and mismatched packages distinct from verified metadata."""

import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from read_host import compare, read_manifest


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

    def test_failed_reads_preserve_cause_without_retry(self):
        url = "https://example.test/panel-sdk-resources.json"
        for status in (401, 403, 404, 503):
            with self.subTest(status=status):
                error = HTTPError(url, status, "failure", {}, BytesIO(b"private response"))
                with patch("read_host.urlopen", side_effect=error) as request:
                    receipt, observed = read_manifest(url)
                self.assertIsNone(observed)
                self.assertEqual(receipt["http_status"], status)
                self.assertNotIn("private response", str(receipt))
                request.assert_called_once_with(url, timeout=15)
        with patch("read_host.urlopen", side_effect=URLError("offline")) as request:
            receipt, observed = read_manifest(url)
        self.assertEqual(receipt["failure"], "transport")
        self.assertIsNone(observed)
        request.assert_called_once()

    def test_invalid_and_oversized_manifest_are_not_package_mismatches(self):
        for payload in (b"[]", b"<html>Login</html>", b"x" * 131073):
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = payload
            with self.subTest(size=len(payload)), patch("read_host.urlopen", return_value=response):
                receipt, observed = read_manifest("https://example.test/panel-sdk-resources.json")
                self.assertEqual(receipt["failure"], "invalid_manifest")
                self.assertIsNone(observed)
                response.read.assert_called_once_with(131073)


if __name__ == "__main__":
    unittest.main()
