import { readFileSync, mkdtempSync, rmSync } from "node:fs";
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
const packageManager = process.env.PANEL_PACKAGE_MANAGER ?? "npm";
if (!["npm", "yarn"].includes(packageManager))
  throw Error("Unsupported panel package manager");
if (
  createHash("sha256").update(readFileSync(artifact)).digest("hex") !==
  provenance.artifacts[basename(artifact)]
)
  throw Error("Panel digest mismatch");
const consumer = mkdtempSync(join(tmpdir(), "langboard-panel-consumer-"));
try {
  execFileSync(
    packageManager,
    packageManager === "yarn"
      ? ["add", "--offline", "--ignore-scripts", "--non-interactive", artifact]
      : ["install", "--ignore-scripts", "--no-audit", "--no-fund", artifact],
    { cwd: consumer, stdio: "inherit" },
  );
  const installed = JSON.parse(
    readFileSync(
      join(consumer, "node_modules/@langboard/app-panel/package.json"),
    ),
  );
  if (installed.version !== provenance.packages["@langboard/app-panel"])
    throw Error("Panel version mismatch");
  const { PanelStateCache, createPanelHost, connectPanel } = await import(
    pathToFileURL(join(consumer, "node_modules/@langboard/app-panel/index.mjs"))
      .href
  );
  const { createSignalInbox } = await import(
    pathToFileURL(join(consumer, "node_modules/@langboard/app-panel/inbox.mjs"))
      .href
  );
  if (
    [createPanelHost, connectPanel, createSignalInbox].some(
      (value) => typeof value !== "function",
    )
  )
    throw Error("Missing panel contract");
  new PanelStateCache();
} finally {
  rmSync(consumer, { recursive: true, force: true });
}
