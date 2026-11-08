import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import * as sdkMetadata from "./vite.panelSdkMetadata.mjs";
const { readPanelHostMetadata, resolvePanelModuleEntry, readHostRuntimeMetadata } = sdkMetadata;

function fixture(options = {}) {
    const root = mkdtempSync(join(tmpdir(), "panel-sdk-export-"));
    const name = options.name ?? "@langboard/app-panel";
    const packageRoot = join(root, "node_modules", name);
    mkdirSync(packageRoot, { recursive: true });
    const entry = options.entry ?? "./index.mjs";
    const source = "export const sdk = true;\n";
    const path = join(packageRoot, entry);
    mkdirSync(join(path, ".."), { recursive: true });
    writeFileSync(path, source);
    const pkg = { name, version: options.version ?? "0.2.7", exports: { ".": { import: entry } } };
    const save = () => writeFileSync(join(packageRoot, "package.json"), JSON.stringify(pkg));
    save();
    return {
        root,
        packageRoot,
        pkg,
        save,
        source,
        path,
        cleanup() {
            rmSync(root, { recursive: true, force: true });
        },
    };
}

for (const entry of ["./index.mjs", "./dist/index.mjs"])
    test(`metadata follows the public export ${entry}`, () => {
        const f = fixture({ entry, version: "0.2.8-rc.1" });
        try {
            const metadata = readPanelHostMetadata(f.root);
            assert.equal(resolvePanelModuleEntry(f.root), f.path);
            assert.deepEqual(metadata, {
                name: "@langboard/app-panel",
                version: "0.2.8-rc.1",
                module_sha256: createHash("sha256").update(f.source).digest("hex"),
            });
        } finally {
            f.cleanup();
        }
    });

test("runtime identity is read from the dedicated installed package", () => {
    const f = fixture({ name: "@langboard/host-runtime", version: "0.1.0-rc.1", entry: "./dist/index.mjs" });
    try {
        assert.equal(readHostRuntimeMetadata(f.root).version, "0.1.0-rc.1");
    } finally {
        f.cleanup();
    }
});

test("invalid identity, version and module exports are rejected", () => {
    const f = fixture();
    try {
        for (const version of ["01.2.3", "1.2", "1.2.3-01", "1.2.3-", "1.2.3+", "1.2.3/else", null]) {
            f.pkg.version = version;
            f.save();
            assert.throws(() => readPanelHostMetadata(f.root), /identity/);
        }
        f.pkg.version = "0.2.7";
        for (const entry of ["../outside.mjs", "file:///tmp/index.mjs", "./index.mjs?key=value", "./index.mjs#fragment", "./index.js", null]) {
            f.pkg.exports["."].import = entry;
            f.save();
            assert.throws(() => readPanelHostMetadata(f.root), /module export/);
        }
        f.pkg.exports["."].import = "./index.mjs";
        f.pkg.name = "other";
        f.save();
        assert.throws(() => readPanelHostMetadata(f.root), /identity/);
    } finally {
        f.cleanup();
    }
});

test("exported symlinks cannot escape package boundaries", () => {
    const f = fixture();
    try {
        const outside = join(f.root, "outside.mjs");
        writeFileSync(outside, "private\n");
        const linked = join(f.packageRoot, "linked.mjs");
        symlinkSync(outside, linked);
        f.pkg.exports["."].import = "./linked.mjs";
        f.save();
        assert.throws(() => readPanelHostMetadata(f.root), /escapes its package/);
    } finally {
        f.cleanup();
    }
});

test("the legacy stable entry remains readable", () => {
    const f = fixture();
    try {
        assert.equal(readPanelHostMetadata(f.root).version, "0.2.7");
    } finally {
        f.cleanup();
    }
});
