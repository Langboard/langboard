import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("./renderClonedPreview.ts", import.meta.url), "utf8");
const javascript = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const module = { exports: {} as typeof import("./renderClonedPreview") };
runInNewContext(javascript, {
    module,
    exports: module.exports,
    require: (name: string) => {
        if (name === "@langboard/core/utils")
            return { Utils: { Type: { isElement: (value: { tagName: string }, tag: string) => value.tagName === tag } } };
        if (name === "tiny-invariant")
            return {
                __esModule: true,
                default: (value: boolean) => {
                    if (!value) throw new Error("invalid element");
                },
            };
        throw new Error(`Unexpected dependency: ${name}`);
    },
});

test("clones a div deeply, retains dimensions, and leaves the source untouched", () => {
    const calls: string[] = [];
    const preview = { tagName: "div", style: { width: "", height: "" }, children: [{ textContent: "child" }] };
    const source = {
        style: { width: "original" },
        getBoundingClientRect: () => {
            calls.push("measure");
            return { width: 180.5, height: 40.25 };
        },
        cloneNode: (deep: boolean) => {
            calls.push("clone");
            assert.equal(deep, true);
            return preview;
        },
    };
    const container = {
        appendChild: (node: unknown) => {
            calls.push("append");
            assert.equal(node, preview);
        },
    };
    module.exports.renderClonedPreview(source as unknown as HTMLDivElement, container as unknown as HTMLElement);
    assert.deepEqual(calls, ["measure", "clone", "append"]);
    assert.equal(preview.style.width, "180.5px");
    assert.equal(preview.style.height, "40.25px");
    assert.equal(preview.children[0].textContent, "child");
    assert.equal(source.style.width, "original");
});

test("rejects a non-div clone before appending", () => {
    const source = { getBoundingClientRect: () => ({ width: 10, height: 20 }), cloneNode: () => ({ tagName: "span" }) };
    let appended = false;
    const container = {
        appendChild: () => {
            appended = true;
        },
    };
    assert.throws(() => module.exports.renderClonedPreview(source as unknown as HTMLDivElement, container as unknown as HTMLElement));
    assert.equal(appended, false);
});
