"""Read public host SDK metadata once; never infer permissions or retry writes."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen


class NoAssetRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_assets(manifest, manifest_url):
    """Read only the two declared same-origin assets, once, without credentials."""
    results = {}
    opener = build_opener(NoAssetRedirect())
    for field, mime_types in (
        ("module_url", {"text/javascript", "application/javascript"}),
        ("css_url", {"text/css"}),
    ):
        value = manifest.get(field)
        url = urljoin(manifest_url, value) if isinstance(value, str) else None
        target, origin = urlsplit(url or ""), urlsplit(manifest_url)
        if (not value or target.scheme != "https" or target.netloc != origin.netloc
                or target.username is not None or target.password is not None
                or target.fragment or target.query):
            results[field] = {"failure": "invalid_asset_url"}
            continue
        try:
            with opener.open(url, timeout=15) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
                content_type = response.headers.get_content_type()
                if (response.status != 200 or not payload or len(payload) > 8 * 1024 * 1024
                        or content_type not in mime_types):
                    results[field] = {"failure": "invalid_asset", "content_type": content_type,
                                      "bytes": len(payload)}
                else:
                    results[field] = {"url": url, "http_status": response.status,
                                      "content_type": content_type, "bytes": len(payload),
                                      "sha256": hashlib.sha256(payload).hexdigest()}
        except HTTPError as error:
            results[field] = {"failure": "http", "http_status": error.code}
            error.close()
        except (URLError, OSError) as error:
            results[field] = {"failure": "transport", "error_type": type(error).__name__}
    return results


def compare(manifest, provenance):
    if not isinstance(manifest, dict):
        raise ValueError("Host resource manifest must be an object")
    host = manifest.get("host_panel_sdk")
    if host is None:
        return "unknown"
    if not isinstance(host, dict) or host.get("name") != "@langboard/app-panel":
        raise ValueError("Invalid host panel SDK identity")
    if not isinstance(host.get("version"), str) or not isinstance(host.get("module_sha256"), str):
        raise ValueError("Incomplete host panel SDK metadata")
    return "match" if (
        host["version"] == provenance["packages"]["@langboard/app-panel"]
        and host["module_sha256"] == provenance["app_panel_module_sha256"]
    ) else "different"


def read_manifest(url):
    try:
        with urlopen(url, timeout=15) as response:
            payload = response.read(131073)
            if len(payload) > 131072:
                raise ValueError("Host resource manifest exceeds 128 KiB")
            manifest = json.loads(payload)
            if not isinstance(manifest, dict):
                raise ValueError("Host resource manifest must be an object")
            return manifest, response.url
    except HTTPError as error:
        status = error.code
        error.close()
        return {"failure": "http", "http_status": status, "next_action": (
            "verify access to this public manifest; app consent is not established"
            if status in (401, 403) else
            "check deployed manifest availability; host SDK identity remains unknown"
            if status == 404 else
            "check host service health; do not restart the app operation"
        )}, None
    except (URLError, OSError) as error:
        return {"failure": "transport", "error_type": type(error).__name__,
                "next_action": "check host connectivity; retain the existing app request ID"}, None
    except ValueError:
        return {"failure": "invalid_manifest",
                "next_action": "check the manifest response and redirects; do not infer SDK compatibility"}, None


def emit_receipt(receipt, output=None):
    """Retain a private, non-overwriting receipt before displaying it."""
    serialized = json.dumps(receipt, indent=2) + "\n"
    if output is not None:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
    print(serialized, end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="HTTPS instance origin, without credentials or query")
    parser.add_argument("--check-assets", action="store_true",
                        help="Read declared JavaScript/CSS once and record bounded public hashes")
    parser.add_argument("--output", type=Path, help="Save a private JSON receipt; existing files are never overwritten")
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("Receipt already exists; inspect it before running another diagnostic")
    origin = urlsplit(args.host)
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.query or origin.fragment
            or origin.path not in ("", "/")):
        parser.error("Expected an HTTPS origin without credentials, path, query or fragment")
    url = args.host.rstrip("/") + "/panel-sdk-resources.json"
    observation = {"requested_url": url,
                   "observed_at": datetime.now(timezone.utc).isoformat()}
    try:
        provenance = json.loads((Path(__file__).resolve().parents[2]
                                / "src/sdk/vendor/provenance.json").read_text())
        reviewed_version = provenance["packages"]["@langboard/app-panel"]
        reviewed_digest = provenance["app_panel_module_sha256"]
        if not isinstance(reviewed_version, str) or not isinstance(reviewed_digest, str):
            raise ValueError("Invalid reviewed SDK identity")
    except (OSError, ValueError, KeyError, TypeError) as error:
        emit_receipt({**observation, "comparison": "unavailable",
                      "failure": "incomplete_checkout", "error_type": type(error).__name__,
                      "next_action": "restore reviewed provenance from this checkout; no host read attempted",
                      "acceptance": "unverified; preparation failed before network access"}, args.output)
        return 1
    manifest, observed_url = read_manifest(url)
    if observed_url is None:
        emit_receipt({**observation, "comparison": "unavailable", **manifest,
                      "acceptance": "unverified; no app operation attempted"}, args.output)
        return 1
    try:
        result = compare(manifest, provenance)
    except ValueError:
        emit_receipt({**observation, "comparison": "unavailable", "failure": "invalid_manifest",
                      "next_action": "check host SDK metadata; do not infer a package mismatch or replay an app request",
                      "acceptance": "unverified; no app operation attempted"}, args.output)
        return 1
    assets = read_assets(manifest, observed_url) if args.check_assets else None
    emit_receipt({
        **observation,
        "observed_url": observed_url,
        "manifest_version": manifest.get("version"),
        "host_panel_sdk": manifest.get("host_panel_sdk"),
        "reviewed_panel_sdk": reviewed_version,
        "reviewed_module_sha256": reviewed_digest,
        "comparison": result,
        **({"assets": assets} if assets is not None else {}),
        "acceptance": "package metadata only; authenticated app operation unverified",
    }, args.output)
    if assets is not None and any("failure" in item for item in assets.values()):
        return 1
    return 0 if result == "match" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (URLError, OSError, ValueError) as error:
        print(json.dumps({"comparison": "unavailable", "error_type": type(error).__name__,
                          "http_status": getattr(error, "code", None),
                          "acceptance": "unverified; reconcile access before retry"}))
        raise SystemExit(1)
