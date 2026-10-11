"""Exercise the real request workflow without credentials or network writes."""
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/request-image-build.yaml'
CODE = yaml.safe_load(WORKFLOW.read_text())['jobs']['request']['steps'][0]['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
SHA = 'a' * 40
TAG = 'refs/tags/image-executor/' + SHA


class Response:
    def __init__(self, status, body):
        self.status, self.body = status, json.dumps(body).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.body[:limit]


class RequestContract(unittest.TestCase):
    def run_request(self, tag=None, dispatch=None, changes=None, annotation=None):
        env = dict(IMAGE_BUILD_REPOSITORY='example/executor', IMAGE_BUILD_WORKFLOW='build.yml',
                   IMAGE_BUILD_REF=TAG, IMAGE_BUILD_COMMIT=SHA, SOURCE_REPOSITORY='example/source',
                   SOURCE_REVISION='b' * 40, IMAGE_BUILD_PROFILE='ui', REQUEST_ID='1-2-1',
                   IMAGE_BUILD_REQUEST_TOKEN='test-only')
        env.update(changes or {})
        responses = [tag or Response(200, {'ref': TAG, 'object': {'type': 'commit', 'sha': SHA}})]
        if annotation is not None:
            responses.append(annotation)
        responses.append(dispatch or Response(204, {}))
        with tempfile.TemporaryDirectory() as directory:
            env['GITHUB_STEP_SUMMARY'] = str(Path(directory) / 'summary')
            with patch.dict(os.environ, env, clear=True), patch('urllib.request.urlopen', side_effect=responses) as calls:
                try:
                    exec(compile(CODE, str(WORKFLOW), 'exec'), {})
                    outcome = 'accepted'
                except SystemExit as error:
                    outcome = str(error)
                return outcome, calls.call_args_list

    def test_verified_tag_is_dispatched_with_source_commit(self):
        outcome, calls = self.run_request()
        self.assertEqual(outcome, 'accepted')
        self.assertEqual(len(calls), 2)
        payload = json.loads(calls[1].args[0].data)
        self.assertEqual(payload['ref'], TAG)
        self.assertEqual(payload['inputs']['source_ref'], 'b' * 40)
        self.assertEqual(payload['inputs']['profile'], 'ui')

    def test_moved_or_wrong_tag_never_dispatches(self):
        for ref, kind, sha in [(TAG, 'commit', 'c' * 40), (TAG, 'tree', SHA), ('refs/tags/other', 'commit', SHA)]:
            with self.subTest(ref=ref, kind=kind, sha=sha):
                outcome, calls = self.run_request(tag=Response(200, {'ref': ref, 'object': {'type': kind, 'sha': sha}}))
                self.assertIn('does not match', outcome)
                self.assertEqual(len(calls), 1)

    def test_annotated_tag_is_dispatched_only_after_exact_commit_verification(self):
        annotation_sha = 'd' * 40
        tag = Response(200, {'ref': TAG, 'object': {'type': 'tag', 'sha': annotation_sha}})
        outcome, calls = self.run_request(tag=tag, annotation=Response(200, {
            'sha': annotation_sha, 'object': {'type': 'commit', 'sha': SHA},
        }))
        self.assertEqual(outcome, 'accepted')
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[1].args[0].full_url.endswith('/git/tags/' + annotation_sha))
        self.assertEqual(json.loads(calls[2].args[0].data)['ref'], TAG)

    def test_wrong_nested_or_unavailable_annotation_never_dispatches(self):
        annotation_sha = 'd' * 40
        for annotation in [
            Response(200, {'sha': annotation_sha, 'object': {'type': 'commit', 'sha': 'c' * 40}}),
            Response(200, {'sha': 'c' * 40, 'object': {'type': 'commit', 'sha': SHA}}),
            Response(200, {'sha': annotation_sha, 'object': {'type': 'tag', 'sha': SHA}}),
            urllib.error.URLError('test'),
        ]:
            outcome, calls = self.run_request(
                tag=Response(200, {'ref': TAG, 'object': {'type': 'tag', 'sha': annotation_sha}}),
                annotation=annotation,
            )
            self.assertNotEqual(outcome, 'accepted')
            self.assertEqual(len(calls), 2)

    def test_missing_commit_mutable_ref_and_disagreement_fail_before_network(self):
        for change in [{'IMAGE_BUILD_COMMIT': ''}, {'IMAGE_BUILD_REF': 'main'}, {'IMAGE_BUILD_COMMIT': 'c' * 40}]:
            with self.subTest(change=change):
                outcome, calls = self.run_request(changes=change)
                self.assertNotEqual(outcome, 'accepted')
                self.assertEqual(calls, [])

    def test_tag_lookup_failure_never_dispatches(self):
        outcome, calls = self.run_request(tag=urllib.error.URLError('test'))
        self.assertIn('no image requested', outcome)
        self.assertEqual(len(calls), 1)

    def test_new_api_receipt_and_unknown_outcome_have_no_retry(self):
        for receipt, accepted in [({'workflow_run_id': 123}, True), ({'workflow_run_id': True}, False), ({}, False)]:
            with self.subTest(receipt=receipt):
                outcome, calls = self.run_request(dispatch=Response(200, receipt))
                self.assertEqual(outcome == 'accepted', accepted)
                self.assertEqual(len(calls), 2)
        outcome, calls = self.run_request(dispatch=TimeoutError())
        self.assertIn('outcome unknown', outcome)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
