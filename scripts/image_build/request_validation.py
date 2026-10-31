"""Submit or inspect validation and product requests using Actions identity."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def required(name, pattern):
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise SystemExit(f"Invalid or missing {name}")
    return value


def current_pull_request(opener, repository, source_ref, revision):
    """Resolve one current same-repository PR; broker retains base/approval authority."""
    if not re.fullmatch(r"refs/heads/review/canary-[A-Za-z0-9._/-]+", source_ref) or ".." in source_ref:
        raise SystemExit("Invalid automatic validation ref")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GitHub pull request credential unavailable")
    branch = source_ref.removeprefix("refs/heads/")
    query = urllib.parse.urlencode({"state": "open", "head": repository.split("/")[0] + ":" + branch, "per_page": 100})
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + "/pulls?" + query,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Langboard-NativeBuild/1.0",
        },
    )
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(1048577)
            if len(raw) > 1048576 or response.headers.get("Link"):
                raise ValueError("Incomplete pull request response")
            pulls = json.loads(raw)
        if not isinstance(pulls, list) or len(pulls) != 1:
            raise ValueError("Expected one open pull request")
        pull = pulls[0]
        if (
            pull["state"] != "open"
            or type(pull["number"]) is not int
            or pull["number"] <= 0
            or pull["head"]["repo"]["full_name"] != repository
            or pull["base"]["repo"]["full_name"] != repository
            or pull["head"]["ref"] != branch
            or pull["head"]["sha"] != revision
        ):
            raise ValueError("Pull request authority mismatch")
        return pull["number"]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SystemExit("Current unique same-repository pull request unavailable; no build requested") from None


def run(read=False, product=False):
    label = "Product" if product else "Validation"
    base = required("IMAGE_BUILD_BROKER_URL", r"https://[^\s]+")
    parsed = urllib.parse.urlsplit(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("Invalid broker URL")
    audience = required("IMAGE_BUILD_OIDC_AUDIENCE", r"[A-Za-z0-9_.:/-]+")
    request_id = required("REQUEST_ID", r"[0-9]+-[0-9]+-[0-9]+")
    executor_commit = required("IMAGE_BUILD_COMMIT", r"[0-9a-f]{40}")
    payload = {
        "schemaVersion": 1,
        "sourceRepository": required("SOURCE_REPOSITORY", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"),
        "sourceCommit": required("SOURCE_REVISION", r"[0-9a-f]{40}"),
        "profile": required("IMAGE_BUILD_PROFILE", r"[a-z0-9][a-z0-9-]{0,47}"),
        "requestId": request_id,
    }
    if product:
        required("REQUEST_ID", r"[1-9][0-9]*-[1-9][0-9]*-[1-9][0-9]*")
        required("IMAGE_BUILD_PROFILE", r"[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?")
        request_id += "-" + payload["profile"]
        payload["requestId"] = request_id
    opener = urllib.request.build_opener(NoRedirect())
    if product:
        payload.update(
            schemaVersion=3,
            sourceRef=required("SOURCE_REF", r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*"),
            sourceChannel=required("SOURCE_CHANNEL", r"default|canary"),
        )
        if ".." in payload["sourceRef"]:
            raise SystemExit("Invalid source ref")
    else:
        payload.update(
            sourceRef=required("SOURCE_REF", r"refs/heads/review/[^\s]+"),
        )
        payload["sourcePr"] = (
            current_pull_request(opener, payload["sourceRepository"], payload["sourceRef"], payload["sourceCommit"])
            if os.environ.get("SOURCE_EVENT") == "push"
            else int(required("SOURCE_PR", r"[1-9][0-9]*"))
        )
    oidc_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    oidc_endpoint = urllib.parse.urlsplit(oidc_url)
    if (
        oidc_endpoint.scheme != "https"
        or not oidc_endpoint.hostname
        or oidc_endpoint.username
        or oidc_endpoint.password
        or oidc_endpoint.fragment
    ):
        raise SystemExit("Actions identity endpoint unavailable")
    token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not token:
        raise SystemExit("Actions identity credential unavailable")
    query = urllib.parse.parse_qsl(oidc_endpoint.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "audience"]
    query.append(("audience", audience))
    assertion_url = urllib.parse.urlunsplit(oidc_endpoint._replace(query=urllib.parse.urlencode(query)))
    deadline = time.monotonic() + 90 if product else None

    def remaining_timeout():
        if deadline is None:
            return 30
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(f"Product deadline exceeded; inspect retained request {request_id}")
        return min(30, remaining)

    def fresh_assertion():
        try:
            identity = urllib.request.Request(assertion_url, headers={"Authorization": "Bearer " + token})
            with opener.open(identity, timeout=remaining_timeout()) as response:
                raw = response.read(16385)
                if len(raw) > 16384:
                    raise ValueError("Identity response too large")
                assertion = json.loads(raw).get("value")
            remaining_timeout()
            if not isinstance(assertion, str) or not assertion or len(assertion) > 16384:
                raise ValueError("Identity response invalid")
            return assertion
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise SystemExit(f"Actions identity unavailable; inspect retained request {request_id}") from None

    assertion = fresh_assertion()
    endpoint = base.rstrip("/") + ("/v1/builds/project-images" if product else "/v1/builds/project-validations")
    if read:
        endpoint += "/" + request_id
    request = urllib.request.Request(
        endpoint,
        method="GET" if read else "POST",
        data=None if read else json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + assertion,
            "Content-Type": "application/json",
            "User-Agent": "Langboard-NativeBuild/1.0",
        },
    )
    if product:
        reconcile_product(opener, request, fresh_assertion, remaining_timeout, deadline, request_id, executor_commit)
        return 0
    for attempt in range(2):
        receipt = None
        try:
            with opener.open(request, timeout=30) as response:
                receipt = json.loads(response.read(16385))
        except urllib.error.HTTPError as error:
            error.close()
            if error.code != 429 and error.code < 500:
                raise SystemExit(f"{label} broker rejected request: HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        if receipt is not None:
            validate_receipt(receipt, request_id, executor_commit, label)
            if receipt["status"] not in {"dispatching", "outcome_unknown"} or attempt or read:
                print(json.dumps(receipt))
                return 0 if receipt["status"] in {"dispatching", "dispatched"} else 1
        if attempt or read:
            raise SystemExit(f"{label} outcome unknown; inspect the same request ID before retrying")
        # Reconcile admission only. Never repeat an ambiguous POST.
        request = urllib.request.Request(
            endpoint if read else endpoint + "/" + request_id,
            headers={"Authorization": "Bearer " + assertion, "User-Agent": "Langboard-NativeBuild/1.0"},
        )


def reconcile_product(opener, request, fresh_assertion, remaining_timeout, deadline, request_id, executor_commit):
    def fetch(current):
        try:
            with opener.open(current, timeout=remaining_timeout()) as response:
                raw = response.read(16385)
                if len(raw) > 16384:
                    raise SystemExit("Product receipt invalid; inspect retained request " + request_id)
                try:
                    receipt = json.loads(raw)
                except ValueError:
                    raise SystemExit("Product receipt invalid; inspect retained request " + request_id) from None
            remaining_timeout()
            validate_receipt(receipt, request_id, executor_commit, "Product")
            if receipt["status"] == "rejected":
                raise SystemExit(f"Product broker rejected request {request_id}")
            return receipt
        except urllib.error.HTTPError as error:
            error.close()
            if error.code != 429 and error.code < 500:
                raise SystemExit(f"Product broker rejected request: HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        return None

    receipt = fetch(request)  # Exactly one POST; all reconciliation uses GET.
    for delay in (0, 2, 4, 8, 16, 30):
        if receipt is not None:
            validate_receipt(receipt, request_id, executor_commit, "Product")
            if receipt["status"] == "rejected":
                raise SystemExit(f"Product broker rejected request {request_id}")
            if receipt["status"] == "dispatched" and receipt["workflowRunId"] is not None:
                print(json.dumps(receipt))
                return
        if time.monotonic() + delay >= deadline:
            break
        if delay:
            time.sleep(delay)
        receipt = fetch(
            urllib.request.Request(
                request.full_url if request.get_method() == "GET" else request.full_url + "/" + request_id,
                headers={"Authorization": "Bearer " + fresh_assertion(), "User-Agent": "Langboard-NativeBuild/1.0"},
            )
        )
    if receipt is not None:
        validate_receipt(receipt, request_id, executor_commit, "Product")
        if receipt["status"] == "dispatched" and receipt["workflowRunId"] is not None:
            print(json.dumps(receipt))
            return
    raise SystemExit(f"Product outcome unknown; inspect retained request {request_id} before any new request")


def validate_receipt(receipt, request_id, executor_commit, label):
    keys = {"schemaVersion", "requestId", "status", "executorCommit", "workflowRunId", "workflowRunUrl"}
    def invalid(reason):
        # Reason labels are local constants; never echo an untrusted broker payload.
        raise SystemExit(f"{label} receipt invalid (reason={reason}); inspect retained request {request_id} before retrying")

    if not isinstance(receipt, dict) or set(receipt) != keys:
        invalid("fields")
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
        invalid("schema")
    if receipt["requestId"] != request_id:
        invalid("request_id")
    if receipt["status"] not in {"dispatching", "dispatched", "outcome_unknown", "rejected"}:
        invalid("status")
    if not isinstance(receipt["executorCommit"], str) or receipt["executorCommit"] != executor_commit:
        invalid("executor")
    run_id, run_url = receipt["workflowRunId"], receipt["workflowRunUrl"]
    if (run_id is None) != (run_url is None) or (
        run_id is not None
        and (
            type(run_id) is not int
            or run_id <= 0
            or not isinstance(run_url, str)
            or (
                run_url != f"https://github.com/yamonco/fractalops/actions/runs/{run_id}"
                if label == "Product"
                else not re.fullmatch(
                    r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/" + str(run_id), run_url
                )
            )
        )
    ):
        invalid("run_identity")


if __name__ == "__main__":
    flags = sys.argv[1:]
    if flags not in ([], ["--read"], ["--product"], ["--product", "--read"]):
        raise SystemExit("Usage: request_validation.py [--product] [--read]")
    raise SystemExit(run(read="--read" in flags, product="--product" in flags))
