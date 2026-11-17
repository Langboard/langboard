const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function compile(file) {
    return ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src", file), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
}

test("initial route preloads only its dashboard code without mounting guarded pages", async () => {
    const constants = {};
    vm.runInNewContext(compile("core/routing/constants.ts"), { exports: constants });
    for (const [pathname, expected] of [
        ["/dashboard", ["./index", "./ProjectPage"]],
        ["/dashboard/projects/all", ["./index", "./ProjectPage"]],
        ["/dashboard/cards", ["./index"]],
        ["/auth/signin", []],
        ["/board/example", []],
        ["/dashboardish/projects/all", []],
    ]) {
        const loaded = [];
        let mounted = false;
        vm.runInNewContext(compile("pages/DashboardPage/Route.tsx"), {
            exports: {},
            window: { location: { pathname } },
            require(name) {
                if (name === "@/core/routing/constants") return constants;
                if (name === "react") return { lazy: () => () => { mounted = true; } };
                if (name === "react/jsx-runtime") return { jsx: () => null, jsxs: () => null };
                if (name.startsWith("./")) {
                    loaded.push(name);
                    return { default: () => { mounted = true; } };
                }
                return {};
            },
        });
        await new Promise(setImmediate);
        assert.deepEqual(loaded, expected, pathname);
        assert.equal(mounted, false, pathname);
    }
});
