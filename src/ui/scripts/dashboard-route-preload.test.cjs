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
        ["/dashboard", ["./index"]],
        ["/dashboard/projects/all", ["./index"]],
        ["/dashboard/cards", ["./index"]],
        ["/auth/signin", []],
        ["/board/example", []],
        ["/dashboardish/projects/all", []],
    ]) {
        const loaded = [];
        let mounted = false;
        const moduleComponent = () => { mounted = true; };
        const lazyComponent = () => { mounted = true; };
        const routeExports = {};
        vm.runInNewContext(compile("pages/DashboardPage/Route.tsx"), {
            exports: routeExports,
            window: { location: { pathname } },
            require(name) {
                if (name === "@/core/routing/constants") return constants;
                if (name === "@/core/routing/preloadableRoutePage") {
                    const helper = {};
                    vm.runInNewContext(compile("core/routing/preloadableRoutePage.tsx"), {
                        exports: helper,
                        require: (name) => name === "react"
                            ? { lazy: () => lazyComponent }
                            : { jsx: (type, props) => ({ type, props }) },
                    });
                    return helper;
                }
                if (name === "react") return { lazy: () => lazyComponent };
                if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
                if (name.startsWith("./")) {
                    loaded.push(name);
                    return { default: moduleComponent };
                }
                return {};
            },
        });
        await new Promise(setImmediate);
        assert.deepEqual(loaded, expected, pathname);
        const dashboard = routeExports.default.routes[0].element.props.children[0];
        assert.equal(dashboard.type().type, expected.length ? moduleComponent : lazyComponent, pathname);
        assert.equal(mounted, false, pathname);
    }
});

test("initial board navigation loads code without mounting the auth guard or board", async () => {
    const constants = {};
    vm.runInNewContext(compile("core/routing/constants.ts"), { exports: constants });
    for (const [pathname, expected] of [
        ["/board/example", ["./RoutePage"]],
        ["/board/example/card", ["./RoutePage"]],
        ["/board/example/wiki/page", ["./RoutePage"]],
        ["/board", []],
        ["/boardish/example", []],
        ["/auth/signin", []],
    ]) {
        const loaded = [];
        let mounted = false;
        const moduleComponent = () => { mounted = true; };
        const lazyComponent = () => { mounted = true; };
        const Guard = () => { throw new Error("AuthGuard mounted during code preload"); };
        const jsx = (type, props) => ({ type, props });
        const helper = {};
        vm.runInNewContext(compile("core/routing/preloadableRoutePage.tsx"), {
            exports: helper,
            require: (name) => name === "react" ? { lazy: () => lazyComponent } : { jsx },
        });
        const routeExports = {};
        vm.runInNewContext(compile("pages/BoardPage/Route.tsx"), {
            exports: routeExports,
            window: { location: { pathname } },
            require(name) {
                if (name === "@/core/routing/constants") return constants;
                if (name === "@/core/routing/preloadableRoutePage") return helper;
                if (name === "@/core/routing/AuthGuard") return { AuthGuard: Guard };
                if (name === "@langboard/core/enums") return { EHttpStatus: { HTTP_404_NOT_FOUND: 404 } };
                if (name === "react") return { lazy: () => lazyComponent };
                if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
                if (name.startsWith("./")) {
                    loaded.push(name);
                    return { default: moduleComponent };
                }
                return {};
            },
        });
        await new Promise(setImmediate);
        assert.deepEqual(loaded, expected, pathname);
        const guardedBoard = routeExports.default.routes[1].element;
        assert.equal(guardedBoard.type, Guard, pathname);
        assert.equal(guardedBoard.props.children.type().type, expected.length ? moduleComponent : lazyComponent, pathname);
        assert.equal(mounted, false, pathname);
    }
});
