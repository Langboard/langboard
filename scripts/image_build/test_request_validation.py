"""Product event/profile routing; transport and security tests live in the shared caller."""

import re
import unittest
from fnmatch import fnmatchcase
from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("api", "worker", "ui", "socket", "graph")
CALLERS = {
    "default": ".github/workflows/request-image-build.yaml",
    **{profile: f".github/workflows/request-validation-{profile}.yaml" for profile in PROFILES},
}


def workflow(profile):
    return yaml.safe_load((ROOT / CALLERS[profile]).read_text())


def evaluate(
    expression,
    event="push",
    ref="refs/heads/review/canary-fixture",
    profile="default",
    validation=False,
    failed=False,
    cancelled=False,
):
    values = {
        "github.event_name": event,
        "github.ref": ref,
        "inputs.profile": profile,
        "inputs.validation_only": validation,
    }
    expression = expression.removeprefix("${{ ").removesuffix(" }}")
    expression = re.sub(
        r"github\.[a-z_]+|inputs\.[a-z_]+|\btrue\b",
        lambda m: repr(True if m[0] == "true" else values[m[0]]),
        expression,
    )
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", "not ", expression)
    return bool(
        eval(
            expression,
            {
                "__builtins__": {},
                "startsWith": str.startswith,
                "failure": lambda: failed,
                "cancelled": lambda: cancelled,
            },
        )
    )


class SharedCallerContracts(unittest.TestCase):
    def test_six_distinct_fixed_review_callers_keep_profile_and_source_identity(self):
        names = set()
        for profile in CALLERS:
            source = workflow(profile)
            job = source["jobs"]["request"]
            names.add(source["name"])
            self.assertRegex(
                job["uses"], r"^yamonco/yamon-gitops/\.github/workflows/remote-project-review.yml@[0-9a-f]{40}$"
            )
            self.assertEqual(job["with"], {"profile": profile, "source_pr": "${{ inputs.source_pr }}"})
            self.assertEqual(source["permissions"], {"contents": "read", "id-token": "write", "pull-requests": "read"})
            self.assertFalse(source["concurrency"]["cancel-in-progress"])
            for forbidden in ("steps", "container", "runs-on", "env", "secrets"):
                self.assertNotIn(forbidden, job)
            self.assertNotIn("workflow_call", source[True])
        self.assertEqual(len(names), 6)

    def test_push_and_manual_review_routes_do_not_publish_products(self):
        for profile in CALLERS:
            job = workflow(profile)["jobs"]["request"]
            self.assertTrue(evaluate(job["if"]))
            for ref in ("refs/heads/main", "refs/heads/deploy/canary", "refs/tags/v1"):
                self.assertFalse(evaluate(job["if"], ref=ref))
            self.assertTrue(evaluate(job["if"], event="workflow_dispatch", validation=True))
            self.assertFalse(evaluate(job["if"], event="pull_request"))

    def test_deploy_push_serializes_all_five_profiles_and_retains_canary_scope(self):
        jobs = workflow("default")["jobs"]
        for index, profile in enumerate(PROFILES):
            job = jobs[f"runtime-{profile}"]
            self.assertEqual(job.get("needs", []), [] if index == 0 else [f"runtime-{PROFILES[index - 1]}"])
            self.assertEqual(job["with"], {"profile": profile, "source_channel": "canary"})
            self.assertRegex(
                job["uses"], r"^yamonco/yamon-gitops/\.github/workflows/remote-project-image.yml@[0-9a-f]{40}$"
            )
            self.assertTrue(evaluate(job["if"], ref="refs/heads/deploy/canary"))
            self.assertFalse(evaluate(job["if"], ref="refs/heads/deploy/canary", failed=True))
            self.assertFalse(evaluate(job["if"], ref="refs/heads/deploy/canary", cancelled=True))
            self.assertFalse(evaluate(job["if"]))

    def test_manual_publication_selects_only_one_runtime_profile(self):
        jobs = workflow("default")["jobs"]
        for selected in PROFILES:
            for profile in PROFILES:
                job = jobs[f"runtime-{profile}"]
                args = {"event": "workflow_dispatch", "profile": selected, "ref": "refs/heads/deploy/canary"}
                self.assertEqual(evaluate(job["if"], **args), selected == profile)
                self.assertFalse(evaluate(job["if"], **args, validation=True))
                self.assertFalse(evaluate(job["if"], event="workflow_dispatch", profile=selected))

    def test_invalid_manual_combinations_fail_before_dispatch(self):
        jobs = workflow("default")["jobs"]
        guard = jobs["reject-validation-profile"]
        for profile in (*PROFILES, "default"):
            for validation in (False, True):
                invalid = (validation and profile != "default") or (not validation and profile == "default")
                args = {"event": "workflow_dispatch", "profile": profile, "validation": validation}
                self.assertEqual(evaluate(guard["if"], **args), invalid)
                if invalid:
                    self.assertFalse(evaluate(jobs["request"]["if"], **args))
                    for runtime in PROFILES:
                        self.assertFalse(
                            evaluate(jobs[f"runtime-{runtime}"]["if"], **args, ref="refs/heads/deploy/canary")
                        )
        self.assertEqual(guard["permissions"], {})
        self.assertIn("exit 1", guard["steps"][0]["run"])

    def test_all_callers_are_in_validation_packaging_and_source_change_filters(self):
        paths = yaml.safe_load((ROOT / ".github/workflows/check-app-contracts.yaml").read_text())[True]["pull_request"][
            "paths"
        ]
        dockerfile = (ROOT / "docker/phoenix-validation.Dockerfile").read_text()
        copies = [
            line.split()[2:-1]
            for line in dockerfile.splitlines()
            if line.startswith("COPY --chown=validation:validation .github/workflows")
        ]
        for caller in (*CALLERS.values(), ".github/workflows/check-app-contracts.yaml"):
            self.assertTrue(any(fnmatchcase(caller, pattern) for pattern in paths), caller)
            self.assertTrue(
                any(
                    caller == source or caller.startswith(source.rstrip("/") + "/") or fnmatchcase(caller, source)
                    for sources in copies
                    for source in sources
                ),
                caller,
            )
        for profile in CALLERS:
            self.assertIn(".github/workflows/**", workflow(profile)[True]["push"]["paths"])

    def test_product_transport_and_executor_pin_are_retired(self):
        for name in ("request_validation.py", "request_products.py"):
            self.assertFalse((Path(__file__).parent / name).exists())
        for caller in CALLERS.values():
            text = (ROOT / caller).read_text()
            for forbidden in (
                "IMAGE_BUILD_COMMIT",
                "IMAGE_BUILD_OIDC_AUDIENCE",
                "python3 scripts/image_build",
                "secrets.",
            ):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
