import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import unittest
import urllib.error
from fnmatch import fnmatchcase
from pathlib import Path
from unittest.mock import patch
import yaml


spec = importlib.util.spec_from_file_location("request_validation", Path(__file__).with_name("request_validation.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


ROOT = Path(__file__).resolve().parents[2]
VALIDATION_CALLERS = {
    "default": ".github/workflows/request-image-build.yaml",
    **{
        profile: f".github/workflows/request-validation-{profile}.yaml"
        for profile in ("api", "worker", "ui", "graph", "socket")
    },
}


class Response(io.BytesIO):
    def __init__(self, value):
        self.headers = {}
        super().__init__(json.dumps(value).encode())


class BrokerClientTest(unittest.TestCase):
    def invoke(self, receipt=None, changes=None, read=False, failure=None, product=False, responses=None, pulls=None):
        env = dict(
            IMAGE_BUILD_BROKER_URL="https://broker.example.invalid",
            IMAGE_BUILD_COMMIT="b" * 40,
            IMAGE_BUILD_OIDC_AUDIENCE="fractalops-project-validation",
            REQUEST_ID="1-2-1",
            SOURCE_REPOSITORY="example/source",
            SOURCE_REVISION="a" * 40,
            SOURCE_REF="refs/heads/review/test",
            SOURCE_PR="688",
            IMAGE_BUILD_PROFILE="default",
            ACTIONS_ID_TOKEN_REQUEST_URL="https://identity.example.invalid/token?audience=old",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="private-request-token",
        )
        env.update(changes or {})
        receipt = receipt or dict(
            schemaVersion=1,
            requestId=env["REQUEST_ID"] + "-" + env["IMAGE_BUILD_PROFILE"] if product else env["REQUEST_ID"],
            status="dispatched",
            executorCommit="b" * 40,
            workflowRunId=42,
            workflowRunUrl="https://github.com/yamonco/fractalops/actions/runs/42"
            if product
            else "https://github.com/example/executor/actions/runs/42",
        )
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(client.urllib.request, "build_opener") as factory,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            opener = factory.return_value
            opener.open.side_effect = (
                ([] if pulls is None else [pulls])
                + [Response({"value": "private-assertion"})]
                + (
                    responses
                    if responses is not None
                    else (
                        [failure, Response({"value": "fresh-assertion"}), Response(receipt)]
                        if product
                        else [failure, Response(receipt)]
                    )
                    if failure
                    else [Response(receipt)]
                )
            )
            try:
                status = client.run(read=read, product=product)
                outcome = str(status)
            except SystemExit as error:
                outcome = str(error)
            return outcome, opener.open.call_args_list, output.getvalue()

    def test_exact_submission_and_safe_receipt(self):
        outcome, calls, output = self.invoke()
        self.assertEqual(outcome, "0")
        self.assertEqual(len(calls), 2)
        self.assertIn("audience=fractalops-project-validation", calls[0].args[0].full_url)
        self.assertNotIn("audience=old", calls[0].args[0].full_url)
        payload = json.loads(calls[1].args[0].data)
        self.assertEqual(
            set(payload),
            {"schemaVersion", "sourceRepository", "sourceCommit", "sourceRef", "sourcePr", "profile", "requestId"},
        )
        self.assertEqual(payload["sourcePr"], 688)
        self.assertNotIn("private", output)

    def test_read_only_uses_same_id_and_fresh_identity(self):
        outcome, calls, _ = self.invoke(read=True)
        self.assertEqual(outcome, "0")
        self.assertEqual(calls[1].args[0].get_method(), "GET")
        self.assertIsNone(calls[1].args[0].data)
        self.assertTrue(calls[1].args[0].full_url.endswith("/1-2-1"))

    def test_invalid_configuration_stops_before_identity(self):
        for changes in [
            {"IMAGE_BUILD_BROKER_URL": "http://broker.invalid"},
            {"IMAGE_BUILD_BROKER_URL": "https://user:password@broker.invalid"},
            {"SOURCE_PR": "0"},
            {"SOURCE_REF": "refs/heads/main"},
        ]:
            outcome, calls, _ = self.invoke(changes=changes)
            self.assertNotEqual(outcome, "0")
            self.assertEqual(calls, [])

    def test_named_profile_is_transmitted_unchanged(self):
        for profile in ["api", "worker", "ui", "socket", "graph", "default"]:
            for product in [False, True]:
                with self.subTest(profile=profile, product=product):
                    changes = {"IMAGE_BUILD_PROFILE": profile}
                    if product:
                        changes.update(SOURCE_REF="refs/heads/deploy/canary", SOURCE_CHANNEL="canary")
                    outcome, calls, _ = self.invoke(changes=changes, product=product)
                    self.assertEqual(outcome, "0")
                    payload = json.loads(calls[1].args[0].data)
                    self.assertEqual(payload["profile"], profile)

    def test_malformed_profile_stops_before_identity(self):
        for profile in ["", "Socket", "socket_1", "socket/validation", "a" * 49, "-socket"]:
            with self.subTest(profile=profile):
                outcome, calls, _ = self.invoke(changes={"IMAGE_BUILD_PROFILE": profile})
                self.assertNotEqual(outcome, "0")
                self.assertEqual(calls, [])

    def test_ambiguous_dispatch_recovers_with_same_id_get_only(self):
        for failure in [TimeoutError(), urllib.error.HTTPError("https://example.invalid", 503, "private", {}, None)]:
            outcome, calls, output = self.invoke(failure=failure)
            self.assertEqual(outcome, "0")
            self.assertEqual(
                [call.args[0].get_method() for call in calls[1:] if "/builds/" in call.args[0].full_url],
                ["POST", "GET"],
            )
            self.assertTrue(calls[2].args[0].full_url.endswith("/1-2-1"))
            self.assertIsNone(calls[2].args[0].data)
            self.assertNotIn("private", output)

    def test_unexpected_or_mismatched_receipt_is_not_printed(self):
        for receipt in [
            {"schemaVersion": 1, "token": "private"},
            dict(
                schemaVersion=1,
                requestId="9-9-9",
                status="dispatched",
                executorCommit="b" * 40,
                workflowRunId=42,
                workflowRunUrl="https://github.com/example/executor/actions/runs/42",
            ),
        ]:
            outcome, calls, output = self.invoke(receipt=receipt)
            self.assertIn("receipt invalid", outcome)
            self.assertEqual(len(calls), 2)
            self.assertEqual(output, "")

    def test_workflow_reuses_federated_client_without_product_credentials(self):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/request-image-build.yaml"
        job = yaml.safe_load(workflow.read_text())["jobs"]["request"]
        steps = {step.get("name"): step for step in job["steps"]}
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertEqual(job["runs-on"], "${{ fromJSON(vars.CI_RUNNER || '\"unconfigured\"') }}")
        self.assertIn("vars.CI_RUNNER != '' && (", job["if"])
        self.assertRegex(job["container"], r"^python:3\.12\.15-slim-bookworm@sha256:[0-9a-f]{64}$")
        self.assertEqual(job["defaults"]["run"]["shell"], "bash")
        bootstrap = steps["Verify standard request runtime"]
        self.assertIn("sys.version_info[:2] == (3, 12)", bootstrap["run"])
        self.assertIn("ssl.create_default_context().get_ca_certs()", bootstrap["run"])
        self.assertIn('("tar", "gzip")', bootstrap["run"])
        self.assertNotIn("apt-get", json.dumps(job))
        self.assertNotIn("DEBIAN_FRONTEND", json.dumps(job))
        checkout = steps["Checkout federated request client"]
        self.assertEqual(checkout["uses"], "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
        self.assertNotIn("if", checkout)
        self.assertFalse(checkout["with"]["persist-credentials"])
        request = steps["Request validation through federated identity"]
        self.assertIn("inputs.validation_only == true ||", request["if"])
        self.assertEqual(request["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
        self.assertEqual(job["permissions"]["pull-requests"], "read")
        self.assertEqual(request["run"], "python3 scripts/image_build/request_validation.py")
        self.assertNotIn("secrets.", json.dumps(request))
        product = steps["Request product through federated identity"]
        self.assertEqual(
            product["if"],
            "github.event_name == 'workflow_dispatch' && inputs.validation_only != true && startsWith(github.ref, 'refs/heads/deploy/')",
        )
        self.assertEqual(product["run"], "python3 scripts/image_build/request_validation.py --product")
        self.assertNotIn("secrets.", json.dumps(job))
        self.assertEqual(job["env"]["SOURCE_CHANNEL"], "canary")
        self.assertEqual(job["env"]["SOURCE_REF"], "${{ github.ref }}")

    def test_workflow_routes_actual_events_and_profiles(self):
        workflow = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / ".github/workflows/request-image-build.yaml").read_text()
        )
        trigger = workflow[True]  # PyYAML's YAML 1.1 spelling of `on`.
        self.assertEqual(trigger["push"]["branches"], ["deploy/**", "review/canary-*"])
        self.assertIn("scripts/image_build/**", trigger["push"]["paths"])
        self.assertIn(".fractalops/image-build.json", trigger["push"]["paths"])
        self.assertIn(".github/workflows/**", trigger["push"]["paths"])
        job = workflow["jobs"]["request"]
        validation, product, batch, artifact = job["steps"][-4:]
        self.assertEqual(
            workflow["concurrency"]["group"], "image-build-request-${{ github.ref }}-${{ inputs.profile || 'default' }}"
        )
        for event, ref, manual, runner, expected in [
            ("push", "review/canary-ui-followup-20261021", False, "runner", (True, True, False, "default", True)),
            ("push", "review/other", False, "runner", (False, False, False, "api", False)),
            ("push", "main", False, "runner", (False, False, False, "api", False)),
            ("push", "deploy/canary", False, "runner", (True, False, False, "api", False)),
            ("workflow_dispatch", "review/other", True, "runner", (True, True, False, "ui", False)),
            ("workflow_dispatch", "review/canary-test", True, "runner", (True, True, False, "ui", False)),
            ("workflow_dispatch", "review/canary-test", False, "runner", (False, False, False, "ui", False)),
            ("workflow_dispatch", "deploy/canary", False, "runner", (True, False, True, "ui", False)),
            ("push", "review/canary-test", False, "", (False, True, False, "default", True)),
        ]:
            with self.subTest(event=event, ref=ref, runner=runner):
                values = {
                    "vars.CI_RUNNER": runner,
                    "github.ref": "refs/heads/" + ref,
                    "github.event_name": event,
                    "inputs.validation_only": manual,
                    "inputs.profile": "ui" if event == "workflow_dispatch" else "",
                    "vars.IMAGE_BUILD_PROFILE": "",
                }

                def evaluate(expression):
                    expression = expression.removeprefix("${{ ").removesuffix(" }}")
                    expression = re.sub(
                        r"vars\.CI_RUNNER|github\.ref|github\.event_name|inputs\.validation_only|inputs\.profile|vars\.IMAGE_BUILD_PROFILE|\btrue\b",
                        lambda match: repr(True if match[0] == "true" else values[match[0]]),
                        expression,
                    )
                    return eval(
                        expression.replace("&&", " and ").replace("||", " or "),
                        {"__builtins__": {}, "startsWith": str.startswith},
                    )

                self.assertEqual(bool(evaluate(batch["if"])), event == "push" and ref.startswith("deploy/"))
                self.assertEqual(batch["run"], "python3 scripts/image_build/request_products.py")
                self.assertEqual(artifact["with"]["path"], "product-request-receipts.json")
                self.assertIn("always()", artifact["if"])
                self.assertEqual(
                    (
                        bool(evaluate(job["if"])),
                        bool(evaluate(validation["if"])),
                        bool(evaluate(product["if"])),
                        evaluate(job["env"]["IMAGE_BUILD_PROFILE"]),
                        bool(evaluate(workflow["concurrency"]["cancel-in-progress"])),
                    ),
                    expected,
                )

    @staticmethod
    def pull():
        return {
            "number": 688,
            "state": "open",
            "head": {"ref": "review/canary-test", "sha": "a" * 40, "repo": {"full_name": "example/source"}},
            "base": {"repo": {"full_name": "example/source"}},
        }

    def automatic(self, pulls, changes=None):
        return self.invoke(
            pulls=pulls,
            changes={
                "SOURCE_EVENT": "push",
                "SOURCE_REF": "refs/heads/review/canary-test",
                "SOURCE_PR": "999",
                "GITHUB_TOKEN": "private-ephemeral-token",
                **(changes or {}),
            },
        )

    def test_automatic_validation_resolves_exact_pr_before_identity(self):
        outcome, calls, output = self.automatic(Response([self.pull()]))
        self.assertEqual(outcome, "0")
        lookup = calls[0].args[0]
        self.assertEqual(lookup.get_method(), "GET")
        self.assertEqual(
            lookup.full_url,
            "https://api.github.com/repos/example/source/pulls?state=open&head=example%3Areview%2Fcanary-test&per_page=100",
        )
        self.assertEqual(lookup.get_header("Authorization"), "Bearer private-ephemeral-token")
        self.assertEqual(calls[0].kwargs["timeout"], 30)
        payload = json.loads(calls[2].args[0].data)
        self.assertEqual(payload["sourcePr"], 688)
        self.assertEqual(payload["sourceCommit"], "a" * 40)
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertTrue(calls[2].args[0].full_url.endswith("/project-validations"))
        self.assertNotIn("private", output)

    def test_each_validation_run_keeps_its_native_identity_without_a_profile_suffix(self):
        request_ids = set()
        for run_id, profile in enumerate(VALIDATION_CALLERS, start=2):
            with self.subTest(profile=profile):
                request_id = f"1-{run_id}-1"
                outcome, calls, output = self.automatic(
                    Response([self.pull()]), {"IMAGE_BUILD_PROFILE": profile, "REQUEST_ID": request_id}
                )
                self.assertEqual(outcome, "0")
                payload = json.loads(calls[2].args[0].data)
                self.assertEqual(payload["schemaVersion"], 1)
                self.assertEqual(payload["profile"], profile)
                self.assertEqual(payload["requestId"], request_id)
                self.assertEqual(json.loads(output)["requestId"], request_id)
                request_ids.add(payload["requestId"])
        self.assertEqual(len(request_ids), 6)

    def test_automatic_validation_rejects_missing_ambiguous_stale_and_fork_prs(self):
        invalid = [[], [self.pull(), self.pull()], {}, [None]]
        for field, value in [("state", "closed"), ("number", True), ("number", 0)]:
            pull = self.pull()
            pull[field] = value
            invalid.append([pull])
        for field, value in [
            ("sha", "c" * 40),
            ("ref", "review/canary-other"),
            ("repo", {"full_name": "fork/source"}),
            ("repo", None),
        ]:
            pull = self.pull()
            pull["head"][field] = value
            invalid.append([pull])
        pull = self.pull()
        pull["base"]["repo"]["full_name"] = "other/source"
        invalid.append([pull])
        for pulls in invalid:
            with self.subTest(pulls=pulls):
                outcome, calls, output = self.automatic(Response(pulls))
                self.assertIn("pull request unavailable", outcome)
                self.assertEqual(len(calls), 1)
                self.assertEqual(output, "")

    def test_automatic_validation_api_failures_are_bounded_and_credential_free(self):
        oversized = io.BytesIO(b" " * 1048577)
        oversized.headers = {}
        paginated = Response([self.pull()])
        paginated.headers["Link"] = '<https://api.github.com/next>; rel="next"'
        for response in [
            TimeoutError(),
            urllib.error.URLError("private"),
            Response("private"),
            io.BytesIO(b"invalid private json"),
            oversized,
            paginated,
        ] + [
            urllib.error.HTTPError("https://api.github.com", code, "private", {}, None)
            for code in [302, 401, 403, 404, 429, 500]
        ]:
            with self.subTest(response=type(response).__name__):
                outcome, calls, output = self.automatic(response)
                self.assertIn("pull request unavailable", outcome)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("private", outcome + output)

    def test_automatic_validation_requires_narrow_ref_and_ephemeral_token(self):
        for changes in [
            {"GITHUB_TOKEN": ""},
            {"SOURCE_REF": "refs/heads/review/test"},
            {"SOURCE_REF": "refs/heads/review/canary-../main"},
        ]:
            outcome, calls, output = self.automatic(Response([self.pull()]), changes)
            self.assertNotEqual(outcome, "0")
            self.assertEqual(calls, [])
            self.assertEqual(output, "")

    @staticmethod
    def product_receipt(**changes):
        value = dict(
            schemaVersion=1,
            requestId="1-2-1-default",
            status="dispatched",
            executorCommit="b" * 40,
            workflowRunId=42,
            workflowRunUrl="https://github.com/yamonco/fractalops/actions/runs/42",
        )
        return {**value, **changes}

    def product_invoke(self, **kwargs):
        return self.invoke(
            changes={"SOURCE_REF": "refs/heads/deploy/canary", "SOURCE_CHANNEL": "canary"}, product=True, **kwargs
        )

    def test_product_null_run_reconciles_same_id_with_fresh_identity(self):
        pending = self.product_receipt(workflowRunId=None, workflowRunUrl=None)
        outcome, calls, output = self.product_invoke(
            responses=[Response(pending), Response({"value": "fresh-assertion"}), Response(self.product_receipt())]
        )
        self.assertEqual(outcome, "0")
        self.assertEqual([c.args[0].get_method() for c in calls], ["GET", "POST", "GET", "GET"])
        self.assertEqual(calls[-1].args[0].headers["Authorization"], "Bearer fresh-assertion")
        self.assertTrue(calls[-1].args[0].full_url.endswith("/1-2-1-default"))
        self.assertNotIn("assertion", output)

    def test_product_pending_exhaustion_never_succeeds_or_reposts(self):
        pending = self.product_receipt(workflowRunId=None, workflowRunUrl=None)
        replies = [Response(pending)]
        for _ in range(6):
            replies += [Response({"value": "fresh"}), Response(pending)]
        with patch.object(client.time, "sleep") as sleep:
            outcome, calls, output = self.product_invoke(responses=replies)
        self.assertIn("outcome unknown", outcome)
        self.assertEqual(sum(c.args[0].get_method() == "POST" for c in calls), 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8, 16, 30])
        self.assertEqual(output, "")

    def test_product_invalid_receipts_fail_without_reconciliation(self):
        for receipt in [
            None,
            {},
            self.product_receipt(executorCommit="c" * 40),
            self.product_receipt(requestId="1-2-1"),
            self.product_receipt(workflowRunId=True),
            self.product_receipt(workflowRunUrl="https://github.com/other/repo/actions/runs/42"),
            self.product_receipt(status="rejected"),
        ]:
            with self.subTest(receipt=receipt):
                outcome, calls, output = self.product_invoke(responses=[Response(receipt)])
                self.assertNotEqual(outcome, "0")
                self.assertEqual(len(calls), 2)
                self.assertEqual(output, "")

    def test_product_explicit_rejection_does_not_reconcile(self):
        for code in [400, 401, 403, 404, 409]:
            outcome, calls, _ = self.product_invoke(
                failure=urllib.error.HTTPError("https://example.invalid", code, "private", {}, None)
            )
            self.assertIn(f"HTTP {code}", outcome)
            self.assertEqual(len(calls), 2)

    def test_product_deadline_bounds_identity_and_http_timeouts(self):
        now = iter(range(0, 200, 10))
        pending = self.product_receipt(workflowRunId=None, workflowRunUrl=None)
        replies = [Response(pending)]
        for _ in range(6):
            replies += [Response({"value": "fresh"}), Response(pending)]
        with patch.object(client.time, "monotonic", side_effect=lambda: next(now)), patch.object(client.time, "sleep"):
            outcome, calls, output = self.product_invoke(responses=replies)
        self.assertTrue("outcome unknown" in outcome or "deadline exceeded" in outcome)
        self.assertEqual(sum(c.args[0].get_method() == "POST" for c in calls), 1)
        self.assertTrue(all(0 < c.kwargs["timeout"] <= 30 for c in calls))
        self.assertEqual(output, "")

    def test_product_exact_envelope_omits_pr_and_uses_separate_endpoint(self):
        changes = {"SOURCE_REF": "refs/heads/deploy/canary", "SOURCE_CHANNEL": "canary", "SOURCE_PR": ""}
        outcome, calls, output = self.invoke(changes=changes, product=True)
        self.assertEqual(outcome, "0")
        payload = json.loads(calls[1].args[0].data)
        self.assertEqual(
            set(payload),
            {"schemaVersion", "sourceRepository", "sourceCommit", "sourceRef", "sourceChannel", "profile", "requestId"},
        )
        self.assertEqual(payload["schemaVersion"], 3)
        self.assertEqual(payload["requestId"], "1-2-1-default")
        self.assertEqual(payload["sourceChannel"], "canary")
        self.assertTrue(calls[1].args[0].full_url.endswith("/v1/builds/project-images"))
        self.assertNotIn("private", output)

    def test_product_read_uses_same_id_and_no_write(self):
        changes = {"SOURCE_REF": "refs/heads/deploy/canary", "SOURCE_CHANNEL": "canary"}
        outcome, calls, _ = self.invoke(changes=changes, product=True, read=True)
        self.assertEqual(outcome, "0")
        request = calls[1].args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertTrue(request.full_url.endswith("/v1/builds/project-images/1-2-1-default"))

    def test_product_invalid_channel_or_ref_never_requests_identity(self):
        for changes in [
            {"SOURCE_CHANNEL": "review"},
            {"SOURCE_CHANNEL": "canary", "SOURCE_REF": "refs/tags/latest"},
            {"SOURCE_CHANNEL": "canary", "SOURCE_REF": "refs/heads/deploy/../main"},
        ]:
            outcome, calls, _ = self.invoke(changes=changes, product=True)
            self.assertNotEqual(outcome, "0")
            self.assertEqual(calls, [])

    def test_default_product_channel_is_transmitted_without_guessing_branch(self):
        outcome, calls, _ = self.invoke(
            changes={"SOURCE_CHANNEL": "default", "SOURCE_REF": "refs/heads/main"}, product=True
        )
        self.assertEqual(outcome, "0")
        self.assertEqual(json.loads(calls[1].args[0].data)["sourceChannel"], "default")

    def test_product_timeout_and_rate_limit_do_not_redispatch(self):
        changes = {"SOURCE_CHANNEL": "canary", "SOURCE_REF": "refs/heads/deploy/canary"}
        for failure in [
            TimeoutError(),
            urllib.error.HTTPError("https://example.invalid", 429, "private", {}, None),
            urllib.error.HTTPError("https://example.invalid", 503, "private", {}, None),
        ]:
            outcome, calls, output = self.invoke(changes=changes, product=True, failure=failure)
            self.assertEqual(outcome, "0")
            self.assertEqual(
                [call.args[0].get_method() for call in calls[1:] if "/builds/" in call.args[0].full_url],
                ["POST", "GET"],
            )
            self.assertTrue(calls[-1].args[0].full_url.endswith("/project-images/1-2-1-default"))
            self.assertNotIn("private", output)

    def test_unresolved_dispatch_stops_after_one_read(self):
        outcome, calls, output = self.invoke(responses=[TimeoutError(), TimeoutError()])
        self.assertIn("outcome unknown", outcome)
        self.assertEqual(
            [call.args[0].get_method() for call in calls[1:] if "/builds/" in call.args[0].full_url], ["POST", "GET"]
        )
        self.assertEqual(output, "")

    def test_explicit_rejection_does_not_reconcile(self):
        for code in [400, 401, 403, 404, 409]:
            with self.subTest(code=code):
                outcome, calls, output = self.invoke(
                    failure=urllib.error.HTTPError("https://example.invalid", code, "private", {}, None)
                )
                self.assertIn(f"HTTP {code}", outcome)
                self.assertEqual(len(calls), 2)
                self.assertEqual(output, "")

    def test_pending_receipt_is_read_once_without_reposting(self):
        for status in ["dispatching", "outcome_unknown"]:
            receipt = dict(
                schemaVersion=1,
                requestId="1-2-1",
                status=status,
                executorCommit="b" * 40,
                workflowRunId=None,
                workflowRunUrl=None,
            )
            outcome, calls, _ = self.invoke(responses=[Response(receipt), Response(receipt)])
            self.assertEqual(outcome, "0" if status == "dispatching" else "1")
            self.assertEqual(
                [call.args[0].get_method() for call in calls[1:] if "/builds/" in call.args[0].full_url],
                ["POST", "GET"],
            )

    def test_invalid_recovery_receipt_is_not_accepted(self):
        outcome, calls, output = self.invoke(responses=[TimeoutError(), Response({"token": "private"})])
        self.assertIn("receipt invalid", outcome)
        self.assertEqual(len(calls), 3)
        self.assertEqual(output, "")

    def test_cancellation_is_not_reconciled(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(responses=[KeyboardInterrupt()])

    def test_unapproved_executor_receipt_is_rejected(self):
        receipt = dict(
            schemaVersion=1,
            requestId="1-2-1",
            status="dispatched",
            executorCommit="c" * 40,
            workflowRunId=42,
            workflowRunUrl="https://github.com/example/executor/actions/runs/42",
        )
        outcome, calls, output = self.invoke(receipt=receipt)
        self.assertIn("receipt invalid", outcome)
        self.assertEqual(len(calls), 2)
        self.assertEqual(output, "")

    def test_redirect_handler_does_not_forward_bearer(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))


class ValidationCallerTest(unittest.TestCase):
    @staticmethod
    def workflow(profile):
        return yaml.safe_load((ROOT / VALIDATION_CALLERS[profile]).read_text())

    @staticmethod
    def evaluate(expression, values):
        expression = expression.removeprefix("${{ ").removesuffix(" }}")
        expression = re.sub(
            r"github\.[a-z_]+|inputs\.[a-z_]+|vars\.[A-Z_]+|\btrue\b",
            lambda match: repr(True if match[0] == "true" else values[match[0]]),
            expression,
        )
        return eval(
            expression.replace("&&", " and ").replace("||", " or "),
            {"__builtins__": {}, "startsWith": str.startswith},
        )

    def test_fixed_callers_use_independent_native_runs_and_the_existing_trusted_transport(self):
        default = self.workflow("default")
        names = set()
        check_names = set()
        for profile in VALIDATION_CALLERS:
            with self.subTest(profile=profile):
                workflow = self.workflow(profile)
                names.add(workflow["name"])
                self.assertNotIn("workflow_call", workflow[True])
                self.assertEqual(set(workflow["jobs"]), {"request"})
                job = workflow["jobs"]["request"]
                check_names.add(job.get("name", "request"))
                self.assertNotIn("uses", job)
                self.assertNotIn("strategy", job)
                self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write", "pull-requests": "read"})
                if profile != "default":
                    self.assertEqual(job["runs-on"], "${{ fromJSON(vars.CI_RUNNER || '\"unconfigured\"') }}")
                    self.assertEqual(
                        job["container"],
                        "python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70",
                    )
                self.assertEqual(
                    job["env"]["REQUEST_ID"],
                    "${{ github.repository_id }}-${{ github.run_id }}-${{ github.run_attempt }}",
                )
                for key, expression in {
                    "SOURCE_REPOSITORY": "${{ github.repository }}",
                    "SOURCE_REVISION": "${{ github.sha }}",
                    "SOURCE_REF": "${{ github.ref }}",
                    "SOURCE_EVENT": "${{ github.event_name }}",
                    "SOURCE_PR": "${{ inputs.source_pr }}",
                    "IMAGE_BUILD_COMMIT": "${{ vars.IMAGE_BUILD_COMMIT }}",
                    "IMAGE_BUILD_BROKER_URL": "${{ vars.IMAGE_BUILD_BROKER_URL }}",
                    "IMAGE_BUILD_OIDC_AUDIENCE": "${{ vars.IMAGE_BUILD_OIDC_AUDIENCE }}",
                }.items():
                    self.assertEqual(job["env"][key], expression)
                self.assertNotIn("secrets.", json.dumps(workflow))
                steps = {step["name"]: step for step in job["steps"]}
                for name in ("Verify standard request runtime", "Checkout federated request client"):
                    self.assertEqual(
                        steps[name], next(step for step in default["jobs"]["request"]["steps"] if step["name"] == name)
                    )
                request = steps["Request validation through federated identity"]
                self.assertEqual(request["run"], "python3 scripts/image_build/request_validation.py")
                self.assertEqual(request["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
                if profile != "default":
                    self.assertEqual(job["name"], f"request-validation-{profile}")
                    self.assertNotIn("if", request)
                    self.assertEqual(job["env"]["IMAGE_BUILD_PROFILE"], profile)
                    self.assertEqual(set(workflow[True]["workflow_dispatch"]["inputs"]), {"source_pr"})
                    self.assertEqual(workflow[True]["push"]["branches"], ["review/canary-*"])
                    self.assertEqual(workflow[True]["push"]["paths"], default[True]["push"]["paths"])
                    self.assertEqual(
                        set(steps),
                        {
                            "Verify standard request runtime",
                            "Checkout federated request client",
                            "Request validation through federated identity",
                        },
                    )
        self.assertEqual(len(names), 6)
        self.assertEqual(default["jobs"]["request"].get("name", "request"), "request")
        self.assertEqual(len(check_names), 6)

    def test_fixed_caller_event_routing_and_cancellation_are_profile_scoped(self):
        groups = set()
        for profile in VALIDATION_CALLERS:
            workflow = self.workflow(profile)
            job = workflow["jobs"]["request"]
            for event, ref, runner, admitted, cancel in [
                ("push", "review/canary-test", "runner", True, True),
                ("push", "review/other", "runner", False, False),
                ("push", "main", "runner", False, False),
                ("workflow_dispatch", "review/canary-test", "runner", True, False),
                ("workflow_dispatch", "review/other", "runner", True, False),
                ("workflow_dispatch", "main", "runner", False, False),
                ("push", "review/canary-test", "", False, True),
            ]:
                with self.subTest(profile=profile, event=event, ref=ref, runner=runner):
                    values = {
                        "github.event_name": event,
                        "github.ref": "refs/heads/" + ref,
                        "vars.CI_RUNNER": runner,
                        "inputs.validation_only": True,
                        "inputs.profile": "default" if event == "workflow_dispatch" else "",
                        "vars.IMAGE_BUILD_PROFILE": "untrusted-profile",
                    }
                    self.assertEqual(bool(self.evaluate(job["if"], values)), admitted)
                    self.assertEqual(bool(self.evaluate(workflow["concurrency"]["cancel-in-progress"], values)), cancel)
                    actual_profile = (
                        self.evaluate(job["env"]["IMAGE_BUILD_PROFILE"], values)
                        if profile == "default"
                        else job["env"]["IMAGE_BUILD_PROFILE"]
                    )
                    if admitted or profile != "default":
                        self.assertEqual(actual_profile, profile)
            group = re.sub(
                r"\$\{\{ (.*?) }}", lambda match: str(self.evaluate(match[1], values)), workflow["concurrency"]["group"]
            )
            groups.add(group)
            if profile != "default":
                for event in ("push", "workflow_dispatch"):
                    values.update({"github.event_name": event, "github.ref": "refs/heads/deploy/canary"})
                    self.assertFalse(self.evaluate(job["if"], values))
        self.assertEqual(len(groups), 6)

    def test_legacy_manual_nondefault_validation_fails_before_the_identity_client(self):
        job = self.workflow("default")["jobs"]["request"]
        steps = job["steps"]
        guard = next(step for step in steps if step["name"] == "Reject unsupported manual validation profile")
        request = next(step for step in steps if step["name"] == "Request validation through federated identity")
        self.assertLess(steps.index(guard), steps.index(request))
        self.assertNotIn("continue-on-error", guard)
        self.assertFalse(
            self.evaluate(
                guard["if"], {"github.event_name": "push", "inputs.validation_only": True, "inputs.profile": "api"}
            )
        )
        for profile in VALIDATION_CALLERS:
            for validation_only in (True, False):
                with self.subTest(profile=profile, validation_only=validation_only):
                    values = {
                        "github.event_name": "workflow_dispatch",
                        "inputs.validation_only": validation_only,
                        "inputs.profile": profile,
                    }
                    reject = bool(self.evaluate(guard["if"], values))
                    self.assertEqual(reject, validation_only and profile != "default")
                    if reject:
                        result = subprocess.run(
                            ["bash", "-e", "-c", guard["run"] + "\nprintf 'OIDC_SUBMITTED\\n'"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("default", result.stdout + result.stderr)
                        self.assertIn("request-validation-", result.stdout + result.stderr)
                        self.assertNotIn("OIDC_SUBMITTED", result.stdout + result.stderr)

    def test_contract_gate_and_integration_image_include_every_validation_caller(self):
        paths = yaml.safe_load((ROOT / ".github/workflows/check-app-contracts.yaml").read_text())[True]["pull_request"][
            "paths"
        ]
        for caller in VALIDATION_CALLERS.values():
            self.assertTrue(any(fnmatchcase(caller, pattern) for pattern in paths), caller)
        dockerfile = (ROOT / "docker/phoenix-validation.Dockerfile").read_text()
        copied_workflows = [
            line.split()[2:-1]
            for line in dockerfile.splitlines()
            if line.startswith("COPY --chown=validation:validation .github/workflows")
        ]
        for caller in (*VALIDATION_CALLERS.values(), ".github/workflows/check-app-contracts.yaml"):
            self.assertTrue(
                any(
                    caller == source or caller.startswith(source.rstrip("/") + "/") or fnmatchcase(caller, source)
                    for sources in copied_workflows
                    for source in sources
                ),
                caller,
            )


if __name__ == "__main__":
    unittest.main()
