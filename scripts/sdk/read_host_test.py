"""Keep unknown hosts and mismatched packages distinct from verified metadata."""

import hashlib
import unittest
from contextlib import redirect_stdout
from email.message import Message
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from read_host import NoAssetRedirect, compare, main, read_assets, read_manifest


class HostReadbackTests(unittest.TestCase):
    def test_cli_does_not_accept_matching_identity_with_broken_assets(self):
        for comparison, assets, expected in (
            ("match", {"module_url": {"failure": "http", "http_status": 404}}, 1),
            ("match", {"module_url": {"http_status": 200}}, 0),
            ("different", {"module_url": {"http_status": 200}}, 2),
        ):
            with (self.subTest(comparison=comparison, expected=expected),
                  patch("sys.argv", ["read_host.py", "https://example.test", "--check-assets"]),
                  patch("read_host.read_manifest", return_value=({}, "https://example.test/manifest.json")),
                  patch("read_host.compare", return_value=comparison),
                  patch("read_host.read_assets", return_value=assets) as check,
                  redirect_stdout(StringIO())):
                self.assertEqual(main(), expected)
                check.assert_called_once()

    def test_asset_receipt_checks_both_declared_resources_once(self):
        responses = []
        for mime in ("text/javascript", "text/css"):
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.headers = Message()
            response.headers["Content-Type"] = mime
            response.read.return_value = b"compiled asset"
            responses.append(response)
        with patch("read_host.build_opener") as factory:
            factory.return_value.open.side_effect = responses
            receipt = read_assets({"module_url": "/widgets.js", "css_url": "/style.css"},
                                  "https://example.test/panel-sdk-resources.json")
        self.assertEqual(factory.return_value.open.call_count, 2)
        for item in receipt.values():
            self.assertEqual(item["sha256"], hashlib.sha256(b"compiled asset").hexdigest())
            self.assertEqual(item["http_status"], 200)
        for response in responses:
            response.read.assert_called_once_with(8 * 1024 * 1024 + 1)

    def test_asset_urls_cannot_send_requests_to_other_origins(self):
        for value in (None, "https://other.test/a.js", "//other.test/a.js",
                      "http://example.test/a.js", "/a.js?token=secret", "/a.js#fragment"):
            with self.subTest(value=value), patch("read_host.build_opener") as factory:
                receipt = read_assets({"module_url": value}, "https://example.test/manifest.json")
                factory.return_value.open.assert_not_called()
                self.assertEqual(receipt["module_url"]["failure"], "invalid_asset_url")
        self.assertIsNone(NoAssetRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_asset_failure_does_not_retry_or_hide_the_other_asset(self):
        url = "https://example.test/panel-sdk-resources.json"
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        response.read.return_value = b"<html>Login</html>"
        with patch("read_host.build_opener") as factory:
            factory.return_value.open.side_effect = [
                HTTPError(url, 503, "failure", {}, BytesIO(b"private response")), response]
            receipt = read_assets({"module_url": "/widgets.js", "css_url": "/style.css"}, url)
        self.assertEqual(factory.return_value.open.call_count, 2)
        self.assertEqual(receipt["module_url"]["http_status"], 503)
        self.assertEqual(receipt["css_url"]["failure"], "invalid_asset")
        self.assertNotIn("private response", str(receipt))

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
