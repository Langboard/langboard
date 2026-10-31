import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("request_products", Path(__file__).with_name("request_products.py"))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class RuntimeBatchTest(unittest.TestCase):
    def invoke(self, failure=None, changes=None, existing=False):
        env = dict(
            SOURCE_EVENT="push",
            SOURCE_REF="refs/heads/deploy/canary",
            SOURCE_REPOSITORY="Langboard/langboard",
            SOURCE_REVISION="a" * 40,
            SOURCE_CHANNEL="canary",
            REQUEST_ID="1-2-1",
            IMAGE_BUILD_COMMIT="b" * 40,
        )
        calls = []

        def request(args, **kwargs):
            profile = kwargs["env"]["IMAGE_BUILD_PROFILE"]
            calls.append((args, kwargs))
            if failure and profile == "ui":
                if isinstance(failure, Exception):
                    raise failure
                receipt = failure
            else:
                receipt = dict(
                    schemaVersion=1,
                    requestId="1-2-1-" + profile,
                    status="dispatched",
                    executorCommit="b" * 40,
                    workflowRunId=40 + len(calls),
                    workflowRunUrl=f"https://github.com/yamonco/fractalops/actions/runs/{40 + len(calls)}",
                )
            return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")

        with tempfile.TemporaryDirectory() as directory:
            receipts = Path(directory) / "receipts.json"
            if existing:
                receipts.write_text('{"retained":true}')
            with (
                patch.dict(os.environ, {**env, **(changes or {})}, clear=True),
                patch.object(batch, "RECEIPTS", receipts),
                patch.object(batch.subprocess, "run", side_effect=request),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                try:
                    outcome = batch.run()
                except (SystemExit, FileExistsError) as error:
                    outcome = str(error)
            return outcome, calls, json.loads(receipts.read_text()) if receipts.exists() else None, output.getvalue()

    def test_all_profiles_have_distinct_receipts_and_one_source(self):
        outcome, calls, manifest, output = self.invoke()
        self.assertEqual(outcome, 0)
        self.assertEqual([call[1]["env"]["IMAGE_BUILD_PROFILE"] for call in calls], list(batch.PROFILES))
        self.assertEqual(manifest["status"], "all_dispatched")
        self.assertEqual(manifest["sourceCommit"], "a" * 40)
        self.assertEqual(len({entry["requestId"] for entry in manifest["profiles"].values()}), 5)
        for args, kwargs in calls:
            self.assertEqual(args[-1], "--product")
            self.assertEqual(kwargs["env"]["SOURCE_REVISION"], manifest["sourceCommit"])
            self.assertEqual(kwargs["timeout"], 100)
        self.assertIn("deployment remain unverified", output)

    def test_failure_preserves_prior_receipts_and_stops_remaining_requests(self):
        for failure in [subprocess.TimeoutExpired("client", 100), subprocess.CalledProcessError(1, "client")]:
            with self.subTest(failure=failure):
                outcome, calls, manifest, _ = self.invoke(failure=failure)
                self.assertIn("incomplete at ui", outcome)
                self.assertEqual(len(calls), 3)
                self.assertEqual(manifest["status"], "incomplete")
                self.assertEqual(manifest["profiles"]["api"]["receipt"]["requestId"], "1-2-1-api")
                self.assertEqual(manifest["profiles"]["ui"]["status"], "outcome_unknown")
                self.assertEqual(manifest["profiles"]["socket"]["status"], "not_requested")
                self.assertEqual(manifest["profiles"]["graph"]["status"], "not_requested")

    def test_invalid_receipt_cannot_advance_batch(self):
        for change in [
            {"requestId": "1-2-1-api"},
            {"executorCommit": "c" * 40},
            {"workflowRunId": None, "workflowRunUrl": None},
            {"status": "rejected"},
        ]:
            receipt = dict(
                schemaVersion=1,
                requestId="1-2-1-ui",
                status="dispatched",
                executorCommit="b" * 40,
                workflowRunId=42,
                workflowRunUrl="https://github.com/yamonco/fractalops/actions/runs/42",
            )
            outcome, calls, manifest, _ = self.invoke(failure={**receipt, **change})
            self.assertIn("incomplete at ui", outcome)
            self.assertEqual(len(calls), 3)
            self.assertEqual(manifest["status"], "incomplete")

    def test_existing_partial_manifest_never_resubmits_or_overwrites(self):
        outcome, calls, manifest, _ = self.invoke(existing=True)
        self.assertNotEqual(outcome, 0)
        self.assertEqual(calls, [])
        self.assertEqual(manifest, {"retained": True})

    def test_review_manual_and_invalid_source_cannot_submit_batch(self):
        for changes in [
            {"SOURCE_REF": "refs/heads/review/canary-test"},
            {"SOURCE_EVENT": "workflow_dispatch"},
            {"SOURCE_CHANNEL": "default"},
            {"SOURCE_REVISION": "invalid"},
        ]:
            outcome, calls, manifest, _ = self.invoke(changes=changes)
            self.assertNotEqual(outcome, 0)
            self.assertEqual(calls, [])
            self.assertIsNone(manifest)


if __name__ == "__main__":
    unittest.main()
