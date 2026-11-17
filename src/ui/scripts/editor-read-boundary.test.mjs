import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { build } from "esbuild";

test("board read entry defers the full editable engine", async () => {
    const result = await build({
        entryPoints: ["src/pages/BoardPage/index.tsx"],
        bundle: true,
        splitting: true,
        format: "esm",
        platform: "browser",
        packages: "external",
        tsconfig: "tsconfig.app.json",
        outdir: "test-results/editor-read-boundary",
        metafile: true,
        write: false,
        loader: { ".svg": "empty", ".css": "empty", ".png": "empty", ".jpg": "empty", ".webp": "empty" },
        logLevel: "silent",
    });
    const outputs = result.metafile.outputs;
    const entry = Object.entries(outputs).find(([, output]) => output.entryPoint === "src/pages/BoardPage/index.tsx");
    assert.ok(entry, "the board entry must build");
    const initial = new Set();
    const visit = (file) => {
        if (initial.has(file)) return;
        initial.add(file);
        for (const imported of outputs[file].imports) {
            if (!imported.external && imported.kind !== "dynamic-import") {
                const resolved = outputs[imported.path] ? imported.path : path.posix.join(path.posix.dirname(file), imported.path);
                assert.ok(outputs[resolved], `unresolved build output ${imported.path}`);
                visit(resolved);
            }
        }
    };
    visit(entry[0]);
    const inputs = new Set([...initial].flatMap((file) => Object.keys(outputs[file].inputs)));
    assert.equal(inputs.has("src/components/Editor/plate-editor.tsx"), false, "reading a board must not load the editable engine");
    assert.equal(inputs.has("src/components/Editor/useCreateEditor.tsx"), false, "reading must not create the interactive editor");
    assert.ok(
        Object.values(outputs).some((output) => output.entryPoint === "src/components/Editor/plate-editor.tsx"),
        "editing must retain its loadable implementation"
    );
});
