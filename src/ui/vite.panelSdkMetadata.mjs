import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

// Resolve the installed transport used by AppPanel, not a historical vendor file.
export function readPanelHostMetadata(uiRoot) {
    const entry = realpathSync(resolve(uiRoot, "node_modules/@langboard/app-panel/index.mjs"));
    const { name, version } = JSON.parse(readFileSync(join(dirname(entry), "package.json"), "utf8"));
    if (name !== "@langboard/app-panel" || !/^\d+\.\d+\.\d+$/.test(version)) {
        throw new Error("Invalid installed panel SDK identity");
    }
    return { name, version, module_sha256: createHash("sha256").update(readFileSync(entry)).digest("hex") };
}
