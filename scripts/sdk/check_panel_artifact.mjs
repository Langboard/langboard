import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";
import { resolve, basename, join } from "node:path";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
const declared = JSON.parse(readFileSync("src/ui/package.json")).dependencies[
  "@langboard/app-panel"
];
if (
  !/^file:\.\.\/sdk\/vendor\/langboard-app-panel-[0-9]+\.[0-9]+\.[0-9]+\.tgz$/.test(
    declared,
  )
)
  throw Error("Invalid panel artifact");
const artifact = resolve("src/ui", declared.slice(5));
const provenance = JSON.parse(readFileSync("src/sdk/vendor/provenance.json"));
if (
  createHash("sha256").update(readFileSync(artifact)).digest("hex") !==
  provenance.artifacts[basename(artifact)]
)
  throw Error("Panel digest mismatch");
const consumer = mkdtempSync(join(tmpdir(), "langboard-panel-consumer-"));
try {
  const packageRoot = join(consumer, "node_modules/@langboard/app-panel");
  mkdirSync(packageRoot, { recursive: true });
  // This dependency-free artifact needs no registry, resolver or lifecycle scripts.
  execFileSync("tar", [
    "-xzf",
    artifact,
    "--strip-components=1",
    "-C",
    packageRoot,
  ]);
  const installed = JSON.parse(readFileSync(join(packageRoot, "package.json")));
  if (installed.version !== provenance.packages["@langboard/app-panel"])
    throw Error("Panel version mismatch");
  if (
    createHash("sha256")
      .update(readFileSync(join(packageRoot, "index.mjs")))
      .digest("hex") !== provenance.app_panel_module_sha256
  )
    throw Error("Panel module digest mismatch");
  const { PanelStateCache, createPanelHost, connectPanel } = await import(
    pathToFileURL(join(packageRoot, "index.mjs")).href
  );
  const { createSignalInbox } = await import(
    pathToFileURL(join(packageRoot, "inbox.mjs")).href
  );
  if (
    [createPanelHost, connectPanel, createSignalInbox].some(
      (value) => typeof value !== "function",
    )
  )
    throw Error("Missing panel contract");
  const cache = new PanelStateCache();
  const maxState = { text: "a".repeat(16 * 1024 - 11) };
  cache.set("state", maxState);
  assert.deepEqual(cache.get("state"), maxState);
  assert.throws(
    () => cache.set("state", { text: maxState.text + "a" }),
    /16 KiB/,
  );

  const page = {
    items: Array.from({ length: 25 }, (_, index) => ({
      resource_uid: `R${String(index + 1).padStart(10, "0")}`,
      connection_uid: `C${String((index % 2) + 1).padStart(10, "0")}`,
      binding_uid: "B0000000001",
      app_key: "yamon-erp",
      resource_type: "workflow",
      external_resource_id:
        "erp:v1:00000000-0000-0000-0000-000000000001:cash_allocation_batch:9a203e89-c1a8-42d8-95d0-f6a0a8b7bafa",
      external_resource_id_truncated: false,
      name: "가".repeat(200),
      access_revision: 1,
      binding_revision: "b".repeat(64),
      health: "unknown",
    })),
    next_cursor: "AQAAAAAAAAAqAAAAAAAAABk",
    limit: 25,
  };
  const responseBytes = Buffer.byteLength(
    JSON.stringify({ type: "response", version: 1, id: 1, result: page }),
  );
  assert.ok(responseBytes > 16 * 1024 && responseBytes < 128 * 1024);
  const previousWindow = globalThis.window;
  const listeners = new Map(),
    parent = {};
  globalThis.window = {
    parent,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
  };
  const frame = {
    contentWindow: {
      postMessage(data, origin, ports) {
        assert.equal(origin, "*");
        const transfer = structuredClone(
          { data, port: ports[0] },
          { transfer: ports },
        );
        listeners.get("message")({
          source: parent,
          origin: "https://host.example",
          data: transfer.data,
          ports: [transfer.port],
        });
      },
    },
    setAttribute(name, value) {
      this[name] = value;
    },
    remove() {
      this.removed = true;
    },
  };
  let result = page,
    calls = 0,
    pendingWork = false,
    workSignal;
  const started = Promise.withResolvers(),
    aborted = Promise.withResolvers();
  const connected = connectPanel({
    expectedHostOrigin: "https://host.example",
  });
  const dispose = createPanelHost({
    frame,
    context: {
      project_uid: "Board000001",
      app_key: "yamon-erp",
      app_version: "0.1.0",
      language: "ko",
    },
    onRequest(operation, params, signal) {
      calls++;
      assert.equal(operation, "resources.list");
      assert.deepEqual(params, { cursor: null });
      if (!pendingWork) return result;
      workSignal = signal;
      started.resolve();
      return new Promise((resolve) =>
        signal.addEventListener(
          "abort",
          () => {
            aborted.resolve();
            resolve(null);
          },
          { once: true },
        ),
      );
    },
  });
  let session;
  try {
    session = await connected;
    assert.equal(frame.sandbox, "allow-scripts allow-forms");
    assert.equal(frame.referrerPolicy, "no-referrer");
    const read = () => session.request("resources.list", { cursor: null });
    assert.deepEqual(await read(), page);
    const overhead = Buffer.byteLength(
      JSON.stringify({
        type: "response",
        version: 1,
        id: 2,
        result: { text: "" },
      }),
    );
    result = { text: "a".repeat(128 * 1024 - overhead) };
    assert.deepEqual(await read(), result);
    result = { text: result.text + "a" };
    await assert.rejects(read(), /^Error: Panel request failed$/);
    result = { items: [] };
    assert.deepEqual(await read(), result);
    const previousCalls = calls;
    await assert.rejects(
      session.request("resources.list", { text: "a".repeat(16 * 1024) }),
      /16 KiB/,
    );
    await assert.rejects(
      session.request("execution.resume", {}),
      /Unsupported/,
    );
    assert.equal(calls, previousCalls);
    session.saveState(maxState);
    assert.throws(
      () => session.saveState({ text: maxState.text + "a" }),
      /16 KiB/,
    );
    pendingWork = true;
    const pending = read();
    const rejected = assert.rejects(pending, /disposed/);
    await started.promise;
    session.dispose();
    await rejected;
    await aborted.promise;
    assert.equal(workSignal.aborted, true);
    console.log(
      JSON.stringify({
        version: installed.version,
        response_bytes: responseBytes,
        response_limit: 128 * 1024,
        request_state_limit: 16 * 1024,
        cancellation: true,
      }),
    );
  } finally {
    session?.dispose();
    dispose();
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
} finally {
  rmSync(consumer, { recursive: true, force: true });
}
