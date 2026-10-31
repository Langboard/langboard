"""Request all canary runtime images sequentially; retain partial admission receipts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from request_validation import required, validate_receipt


PROFILES = ("api", "worker", "ui", "socket", "graph")
RECEIPTS = Path("product-request-receipts.json")


def run():
    source_ref = required("SOURCE_REF", r"refs/heads/deploy/[A-Za-z0-9][A-Za-z0-9._/-]*")
    if os.environ.get("SOURCE_EVENT") != "push" or ".." in source_ref:
        raise SystemExit("Runtime batch requires a deployment push")
    repository = required("SOURCE_REPOSITORY", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    revision = required("SOURCE_REVISION", r"[0-9a-f]{40}")
    request_id = required("REQUEST_ID", r"[1-9][0-9]*-[1-9][0-9]*-[1-9][0-9]*")
    executor = required("IMAGE_BUILD_COMMIT", r"[0-9a-f]{40}")
    manifest = {
        "sourceRepository": repository,
        "sourceCommit": revision,
        "sourceRef": source_ref,
        "sourceChannel": required("SOURCE_CHANNEL", r"canary"),
        "status": "requesting",
        "profiles": {
            profile: {"requestId": request_id + "-" + profile, "status": "not_requested"} for profile in PROFILES
        },
    }
    # A retained partial run is reconciled by its IDs, never submitted again.
    with RECEIPTS.open("x") as output:
        output.write(json.dumps(manifest, indent=2) + "\n")

    def save():
        temporary = RECEIPTS.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.replace(RECEIPTS)

    for profile, entry in manifest["profiles"].items():
        entry["status"] = "outcome_unknown"
        save()
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("request_validation.py")), "--product"],
                env={**os.environ, "IMAGE_BUILD_PROFILE": profile},
                capture_output=True,
                text=True,
                timeout=100,
                check=True,
            )
            receipt = json.loads(result.stdout)
            validate_receipt(receipt, entry["requestId"], executor, "Product")
            if receipt["status"] != "dispatched" or receipt["workflowRunId"] is None:
                raise ValueError("Unresolved admission")
        except (subprocess.SubprocessError, OSError, ValueError, SystemExit):
            manifest["status"] = "incomplete"
            save()
            raise SystemExit(f"Runtime request incomplete at {profile}; reconcile retained IDs in {RECEIPTS}") from None
        entry.update(status="dispatched", receipt=receipt)
        save()
    manifest["status"] = "all_dispatched"
    save()
    print(json.dumps(manifest))
    print("All five requests dispatched. Image publication, matching digests and canary deployment remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
