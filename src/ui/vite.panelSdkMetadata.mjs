import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { join, resolve, relative, isAbsolute, sep } from "node:path";

const numericVersion = "(?:0|[1-9]\\d*)";
const prereleaseIdentifier = "(?:0|[1-9]\\d*|\\d*[A-Za-z-][0-9A-Za-z-]*)";
const semver = new RegExp(
    `^${numericVersion}\\.${numericVersion}\\.${numericVersion}` +
        `(?:-${prereleaseIdentifier}(?:\\.${prereleaseIdentifier})*)?` +
        "(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$"
);

// Resolve the installed package's public ESM entry, including bundled distributions.
// Package links are supported, but its exported module must remain inside that package.
function installedSdk(uiRoot, expectedName) {
    const packageRoot = realpathSync(resolve(uiRoot, "node_modules", expectedName));
    const pkg = JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8"));
    if (pkg.name !== expectedName || typeof pkg.version !== "string" || pkg.version.length > 256 || !semver.test(pkg.version)) {
        throw new Error("Invalid installed panel SDK identity");
    }
    const exported = pkg.exports?.["."];
    const publicEntry = typeof exported === "string" ? exported : exported?.import;
    if (typeof publicEntry !== "string" || !publicEntry.startsWith("./") || !publicEntry.endsWith(".mjs") || /[\\?#]/.test(publicEntry)) {
        throw new Error("Invalid panel SDK module export");
    }
    const entry = realpathSync(resolve(packageRoot, publicEntry));
    const location = relative(packageRoot, entry);
    if (!location || location === ".." || location.startsWith(`..${sep}`) || isAbsolute(location)) {
        throw new Error("Panel SDK module escapes its package");
    }
    return {
        entry,
        metadata: { name: pkg.name, version: pkg.version, module_sha256: createHash("sha256").update(readFileSync(entry)).digest("hex") },
    };
}

export function resolvePanelModuleEntry(uiRoot) {
    return installedSdk(uiRoot, "@langboard/app-panel").entry;
}

export function readPanelHostMetadata(uiRoot) {
    return installedSdk(uiRoot, "@langboard/app-panel").metadata;
}

export function resolveHostRuntimeModuleEntry(uiRoot) {
    return installedSdk(uiRoot, "@langboard/host-runtime").entry;
}

export function readHostRuntimeMetadata(uiRoot) {
    return installedSdk(uiRoot, "@langboard/host-runtime").metadata;
}
