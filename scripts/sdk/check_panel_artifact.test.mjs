import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const script = fileURLToPath(new URL("./check_panel_artifact.mjs", import.meta.url));

test("the reviewed SDK artifact passes from an unrelated working directory", () => {
  const result = spawnSync(process.execPath, [script], { cwd: tmpdir(), encoding: "utf8", timeout: 15000 });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).cancellation, true);
});

test("partial snapshots report missing prerequisites before running tests", () => {
  const root = mkdtempSync(join(tmpdir(), "langboard-sdk-prerequisites-"));
  try {
    const target = join(root, "scripts/sdk/check_panel_artifact.mjs");
    mkdirSync(dirname(target), { recursive: true });
    copyFileSync(script, target);
    const run = () => {
      const result = spawnSync(process.execPath, [target], { cwd: tmpdir(), encoding: "utf8", timeout: 15000 });
      assert.equal(result.status, 1);
      assert.equal(result.stdout, "");
      const receipt = JSON.parse(result.stderr);
      assert.equal(receipt.failure, "incomplete_checkout");
      assert.equal(receipt.tests, "not_run");
      return receipt;
    };
    const missing = ["src/ui/package.json", "src/ui/vite.panelSdkMetadata.mjs", "src/sdk/vendor/provenance.json"];
    assert.deepEqual(run().missing_files, missing);
    for (const file of missing) {
      mkdirSync(dirname(join(root, file)), { recursive: true });
      writeFileSync(join(root, file), file.endsWith("package.json")
        ? JSON.stringify({ dependencies: { "@langboard/app-panel": "file:../sdk/vendor/langboard-app-panel-0.2.7.tgz" } })
        : "");
    }
    // The missing archive must be reported before parsing/importing other files.
    assert.deepEqual(run().missing_files, ["src/sdk/vendor/langboard-app-panel-0.2.7.tgz"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
