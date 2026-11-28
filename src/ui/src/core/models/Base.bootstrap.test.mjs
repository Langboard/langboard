import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

test("base models bootstrap without loading authentication providers", async () => {
    const result = await build({
        absWorkingDir: fileURLToPath(new URL("../../../", import.meta.url)),
        entryPoints: ["src/core/models/Base.ts"],
        tsconfig: "tsconfig.app.json",
        bundle: true,
        format: "esm",
        platform: "browser",
        define: { "process.env": "{}" },
        loader: { ".svg": "empty", ".css": "empty", ".png": "empty" },
        write: false,
        metafile: true,
    });
    const inputs = Object.keys(result.metafile.inputs);
    assert.equal(inputs.some((path) => /\/(AuthProvider\.tsx|AuthStore\.ts)$/.test(path)), false,
        "BaseModel must not load auth providers through model socket subscriptions");
    const { BaseModel } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
    class Model extends BaseModel {
        static get MODEL_NAME() { return "ProjectCard"; }
    }
    const now = new Date();
    const first = Model.fromOne({ uid: "bootstrap", title: "before", created_at: now, updated_at: now });
    assert.equal(Model.fromOne({ uid: "bootstrap", title: "after", created_at: now, updated_at: now }), first);
    assert.equal(Model.getModel("bootstrap"), first);
    assert.equal(first.getValue("title"), "after");
    Model.deleteModel("bootstrap");
    assert.equal(Model.getModel("bootstrap"), undefined);
});
