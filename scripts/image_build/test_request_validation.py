import contextlib
import importlib.util
import io
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("request_validation", Path(__file__).with_name("request_validation.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class BrokerClientTest(unittest.TestCase):
    def invoke(self, receipt=None, changes=None, read=False, failure=None, responses=None):
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
            requestId="1-2-1",
            status="dispatched",
            executorCommit="b" * 40,
            workflowRunId=42,
            workflowRunUrl="https://github.com/example/executor/actions/runs/42",
        )
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(client.urllib.request, "build_opener") as factory,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            opener = factory.return_value
            opener.open.side_effect = [Response({"value": "private-assertion"})] + (
                responses if responses is not None else [failure, Response(receipt)] if failure else [Response(receipt)]
            )
            try:
                status = client.run(read=read)
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
        self.assertEqual(calls[1].args[0].get_header("If-match"), '"' + "b" * 40 + '"')
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
            with self.subTest(profile=profile):
                outcome, calls, _ = self.invoke(changes={"IMAGE_BUILD_PROFILE": profile})
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
            self.assertEqual([call.args[0].get_method() for call in calls[1:]], ["POST", "GET"])
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
        import yaml

        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/request-image-build.yaml"
        job = yaml.safe_load(workflow.read_text())["jobs"]["request"]
        steps = {step.get("name"): step for step in job["steps"]}
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertEqual(job["runs-on"], "${{ fromJSON(vars.IMAGE_BUILD_REQUEST_RUNNER || vars.CI_RUNNER || '\"unconfigured\"') }}")
        self.assertIn("vars.IMAGE_BUILD_REQUEST_RUNNER != '' || vars.CI_RUNNER != ''", job["if"])
        self.assertNotIn("container", job)
        self.assertEqual(job["defaults"]["run"]["shell"], "bash")
        bootstrap = steps["Prepare standard Python request runtime"]
        self.assertEqual(bootstrap["uses"], "actions/setup-python@v5")
        self.assertEqual(bootstrap["with"]["python-version"], "3.12")
        checkout = steps["Checkout federated request client"]
        self.assertNotIn("if", checkout)
        self.assertFalse(checkout["with"]["persist-credentials"])
        request = steps["Request validation through federated identity"]
        self.assertEqual(request["if"], "inputs.validation_only == true")
        self.assertEqual(request["run"], "python3 scripts/image_build/request_validation.py")
        self.assertNotIn("secrets.", json.dumps(request))
        self.assertNotIn("Request product through federated identity", steps)
        self.assertNotIn("secrets.", json.dumps(job))

    def test_product_uses_pinned_public_shared_workflow(self):
        import yaml

        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/request-image-build.yaml"
        job = yaml.safe_load(workflow.read_text())["jobs"]["product"]
        self.assertEqual(job["uses"], "yamonco/yamon-gitops/.github/workflows/remote-project-image.yml@1f0e32c313ef4e5652a9e72412dc6bab17fa521f")
        self.assertEqual(job["with"]["source_channel"], "canary")
        self.assertEqual(job["with"]["profile"], "${{ inputs.profile || vars.IMAGE_BUILD_PROFILE || 'api' }}")
        self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write"})
        self.assertNotIn("secrets", job)
        self.assertNotIn("steps", job)
        self.assertIn("inputs.validation_only != true", job["if"])
        self.assertIn("refs/heads/deploy/", job["if"])

    def test_unresolved_dispatch_stops_after_one_read(self):
        outcome, calls, output = self.invoke(responses=[TimeoutError(), TimeoutError()])
        self.assertIn("outcome unknown", outcome)
        self.assertEqual([call.args[0].get_method() for call in calls[1:]], ["POST", "GET"])
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
            self.assertEqual([call.args[0].get_method() for call in calls[1:]], ["POST", "GET"])

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


if __name__ == "__main__":
    unittest.main()
