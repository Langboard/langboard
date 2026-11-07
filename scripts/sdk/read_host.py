"""Read public host SDK metadata once; never infer permissions or retry writes."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def compare(manifest, provenance):
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
    with urlopen(url, timeout=15) as response:
        payload = response.read(131073)
        if len(payload) > 131072:
            raise ValueError("Host resource manifest exceeds 128 KiB")
        manifest = json.loads(payload)
        observed_url = response.url
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
