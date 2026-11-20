import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { URL } from "node:url";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const ts = require("typescript");
function load(path, dependencies) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    const compiled = ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const exports = {};
    vm.runInNewContext(compiled, { exports, require: (name) => dependencies[name] ?? require(name) });
    return exports.default;
}

const translations = { useTranslation: () => [(key) => key] };
const LoadError = load("./BoardLoadError.tsx", {
    "@/components/base/Box": { default: "section" },
    "@/components/base/Button": { default: ({ children, variant, size, ...props }) => React.createElement("button", props, children) },
    "react-i18next": translations,
});
function render(query) {
    const Page = load("./BoardWikiPage.tsx", {
        "@/controllers/api/wiki/useGetWikis": { default: () => ({ isFetching: false, refetch: () => {}, ...query }) },
        "@/controllers/api/WorkspaceSummaryInvalidation": { acquireWorkspaceSummaryTopics: () => () => {} },
        "@/core/helpers/setupApiErrorHandler": { default: () => ({ handle: () => {} }) },
        "@/core/hooks/usePageNavigate": { usePageNavigateRef: () => () => {} },
        "@/core/providers/BoardWikiProvider": { BoardWikiProvider: ({ children }) => children },
        "@/core/providers/SocketProvider": { useSocket: () => ({}) },
        "@/core/routing/constants": { ROUTES: {} },
        "@/pages/BoardPage/components/wiki/BoardWikiUnsavedProvider": { BoardWikiUnsavedProvider: ({ children }) => children },
        "@/pages/BoardPage/components/wiki/WikiList": {
            default: () => React.createElement("article", null, "saved wiki"),
            SkeletonWikiList: () => React.createElement("div", { role: "status" }, "loading wiki"),
        },
        "@langboard/core/enums": { EHttpStatus: {}, ESocketTopic: {} },
        "react-i18next": translations,
        "./BoardLoadError": { default: LoadError },
    });
    return renderToStaticMarkup(React.createElement(Page, { project: { uid: "board" }, currentUser: {} }));
}

test("initial request failure ends the wiki skeleton and exposes retry", () => {
    const html = render({ error: new Error("private transport detail") });
    assert.match(html, /role="alert"/);
    assert.match(html, /dashboard.Could not load wikis/);
    assert.match(html, /common.Retry/);
    assert.doesNotMatch(html, /loading wiki|private transport detail|saved wiki/);
});

test("retry in flight cannot be submitted twice and retains the error boundary", () => {
    const html = render({ error: new Error("timeout"), isFetching: true });
    assert.match(html, /disabled=""/);
    assert.doesNotMatch(html, /loading wiki/);
});

test("a failed refresh preserves the loaded wiki alongside retry", () => {
    const html = render({ data: { wikis: [], project_members: [] }, error: new Error("offline") });
    assert.match(html, /role="alert"/);
    assert.match(html, /saved wiki/);
    assert.doesNotMatch(html, /loading wiki/);
});

test("pending initial load and successful recovery render their respective content", () => {
    assert.match(render({ isFetching: true }), /loading wiki/);
    const html = render({ data: { wikis: [], project_members: [] } });
    assert.match(html, /saved wiki/);
    assert.doesNotMatch(html, /role="alert"|loading wiki/);
});

test("wiki transport uses the query cancellation signal and a finite request budget", async () => {
    let captured;
    const hook = load("../../controllers/api/wiki/useGetWikis.ts", {
        "@langboard/core/constants": { Routing: { API: { BOARD: { WIKI: { GET_ALL: "/board/{uid}/wikis" } } } } },
        "@langboard/core/utils": { Utils: { String: { format: (url, args) => url.replace("{uid}", args.uid) } } },
        "@/core/helpers/Api": {
            api: {
                get: async (url, config) => {
                    captured = { url, config };
                    throw Error("timeout");
                },
            },
        },
        "@/core/helpers/QueryMutation": { useQueryMutation: () => ({ query: (_, execute) => execute }) },
        "@/core/stores/AuthStore": { default: { getState: () => ({}) } },
        "@/controllers/api/WorkspaceSummaryInvalidation": { workspaceSummaryAuthScope: () => "wiki-test-scope" },
        "@/core/models": {
            ProjectWiki: { Model: { fromArray: () => assert.fail("failed requests must not replace wiki models") } },
            User: { Model: { fromArray: () => assert.fail("failed requests must not replace members") } },
        },
    });
    const signal = new globalThis.AbortController().signal;
    await assert.rejects(hook({ project_uid: "board" })({ signal }), /timeout/);
    assert.equal(captured.url, "/board/board/wikis");
    assert.equal(captured.config.signal, signal);
    assert.equal(captured.config.timeout, 15_000);
});
