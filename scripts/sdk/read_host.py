"""Read public host SDK metadata once; never infer permissions or retry writes."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="HTTPS instance origin, without credentials or query")
    args = parser.parse_args()
    origin = urlsplit(args.host)
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.query or origin.fragment
            or origin.path not in ("", "/")):
        parser.error("Expected an HTTPS origin without credentials, path, query or fragment")
    url = args.host.rstrip("/") + "/panel-sdk-resources.json"
    manifest, observed_url = read_manifest(url)
    if observed_url is None:
        print(json.dumps({"requested_url": url,
                          "observed_at": datetime.now(timezone.utc).isoformat(),
                          "comparison": "unavailable", **manifest,
                          "acceptance": "unverified; no app operation attempted"}, indent=2))
        return 1
    provenance = json.loads((Path(__file__).resolve().parents[2]
                             / "src/sdk/vendor/provenance.json").read_text())
    result = compare(manifest, provenance)
    print(json.dumps({
        "requested_url": url,
        "observed_url": observed_url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "manifest_version": manifest.get("version"),
        "host_panel_sdk": manifest.get("host_panel_sdk"),
        "reviewed_panel_sdk": provenance["packages"]["@langboard/app-panel"],
        "reviewed_module_sha256": provenance["app_panel_module_sha256"],
        "comparison": result,
        "acceptance": "package metadata only; authenticated app operation unverified",
    }, indent=2))
    return 0 if result == "match" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (URLError, OSError, ValueError) as error:
        print(json.dumps({"comparison": "unavailable", "error_type": type(error).__name__,
                          "http_status": getattr(error, "code", None),
                          "acceptance": "unverified; reconcile access before retry"}))
        raise SystemExit(1)
