import contextlib
import importlib.util
import io
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import yaml


spec = importlib.util.spec_from_file_location("request_validation", Path(__file__).with_name("request_validation.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class BrokerClientTest(unittest.TestCase):
    def invoke(self, receipt=None, changes=None, read=False, failure=None):
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
            opener.open.side_effect = [Response({"value": "private-assertion"}), failure or Response(receipt)]
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
            {"IMAGE_BUILD_PROFILE": "api"},
            {"SOURCE_PR": "0"},
            {"SOURCE_REF": "refs/heads/main"},
        ]:
            outcome, calls, _ = self.invoke(changes=changes)
            self.assertNotEqual(outcome, "0")
            self.assertEqual(calls, [])

    def test_ambiguous_dispatch_never_retries(self):
        for failure in [TimeoutError(), urllib.error.HTTPError("https://example.invalid", 503, "private", {}, None)]:
            outcome, calls, output = self.invoke(failure=failure)
            self.assertIn("outcome unknown", outcome)
            self.assertEqual(len(calls), 2)
            self.assertEqual(output, "")

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

    def test_workflow_separates_federated_validation_from_product_credentials(self):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/request-image-build.yaml"
        job = yaml.safe_load(workflow.read_text())["jobs"]["request"]
        steps = {step.get("name"): step for step in job["steps"]}
        self.assertEqual(job["permissions"]["id-token"], "write")
        checkout = steps["Checkout validation request client"]
        self.assertEqual(checkout["if"], "inputs.validation_only == true")
        self.assertFalse(checkout["with"]["persist-credentials"])
        request = steps["Request validation through federated identity"]
        self.assertEqual(request["if"], "inputs.validation_only == true")
        self.assertEqual(request["run"], "python3 scripts/image_build/request_validation.py")
        self.assertNotIn("secrets.", json.dumps(request))
        self.assertEqual(steps["Request the approved immutable image"]["if"], "inputs.validation_only != true")
        self.assertEqual(job["env"]["SOURCE_REF"], "${{ github.ref }}")

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
