import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { URL } from "node:url";
import assert from "node:assert/strict";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("./useArchiveCard.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function load({ card, wiki, put }) {
    const removed = [];
    const dependencies = {
        "@langboard/core/constants": { Routing: { API: { BOARD: { CARD: { ARCHIVE: "/board/{uid}/card/{card_uid}/archive" } } } } },
        "@langboard/core/utils": {
            Utils: { String: { format: (value, params) => value.replace("{uid}", params.uid).replace("{card_uid}", params.card_uid) } },
        },
        "@/core/helpers/Api": { api: { put } },
        "@/core/helpers/QueryMutation": { useQueryMutation: () => ({ mutate: (_, action) => action }) },
        "@/core/models": {
            ProjectCard: { Model: { getModel: (uid) => (card?.uid === uid ? card : undefined) } },
            ProjectWiki: { Model: { getModel: (uid) => (wiki?.uid === uid ? wiki : undefined) } },
        },
        "@/core/helpers/ModelHelper": { deleteCardModel: (...args) => removed.push(args) },
    };
    const exports = {};
    vm.runInNewContext(compiled, { exports, require: (name) => dependencies[name] });
    return { run: exports.default(), removed };
}

const params = { project_uid: "board", card_uid: "linked" };
const linked = () => ({ uid: "linked", project_uid: "board", source_type: "project_wiki", source_uid: "wiki" });
const original = () => ({ uid: "wiki", project_uid: "board", linked_card_uid: "linked", content: "preserve original" });

test("confirmed unlink clears the local reference without waiting for realtime delivery", async () => {
    let confirm;
    const wiki = original();
    const { run, removed } = load({
        card: linked(),
        wiki,
        put: (url) => {
            assert.equal(url, "/board/board/card/linked/archive");
            return new Promise((resolve) => {
                confirm = resolve;
            });
        },
    });
    const pending = run(params);
    assert.equal(removed.length, 0);
    assert.equal(wiki.linked_card_uid, "linked");
    confirm({ data: "confirmed" });
    assert.equal(await pending, "confirmed");
    assert.equal(wiki.linked_card_uid, undefined);
    assert.equal(wiki.content, "preserve original");
    assert.equal(JSON.stringify(removed), JSON.stringify([["linked", true]]));
});

test("failed unlink preserves card and original link", async () => {
    const wiki = original();
    const { run, removed } = load({
        card: linked(),
        wiki,
        put: async () => {
            throw Error("forbidden");
        },
    });
    await assert.rejects(run(params), /forbidden/);
    assert.equal(wiki.linked_card_uid, "linked");
    assert.equal(removed.length, 0);
});

test("normal archives, foreign scope and an already removed model do not run linked cleanup", async () => {
    for (const card of [{ ...linked(), source_type: "card" }, { ...linked(), project_uid: "other" }, undefined]) {
        const wiki = original();
        const { run, removed } = load({ card, wiki, put: async () => ({ data: "confirmed" }) });
        await run(params);
        assert.equal(removed.length, 0);
        assert.equal(wiki.linked_card_uid, "linked");
    }
});

test("a newly relinked or foreign original remains untouched by the old confirmation", async () => {
    for (const wiki of [{ ...original(), linked_card_uid: "new-link" }, { ...original(), project_uid: "other" }, undefined]) {
        const before = JSON.stringify(wiki);
        const { run, removed } = load({ card: linked(), wiki, put: async () => ({ data: "confirmed" }) });
        await run(params);
        assert.equal(JSON.stringify(wiki), before);
        assert.equal(removed.length, 1);
    }
});
