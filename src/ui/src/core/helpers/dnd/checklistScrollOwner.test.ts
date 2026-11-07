import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const noop = () => {};
const viewport = {};

function load(path: string, dependencies: Record<string, unknown>) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    const javascript = ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    const module = { exports: {} as Record<string, (...args: unknown[]) => unknown> };
    runInNewContext(javascript, {
        module,
        exports: module.exports,
        require: (name: string) => {
            assert(name in dependencies, `Unexpected dependency: ${name}`);
            return dependencies[name];
        },
    });
    return module.exports;
}

test("a checklist delegates its shared viewport to the group", () => {
    const effects: (() => unknown)[] = [];
    const registrations: { scrollable?: unknown }[] = [];
    const source = readFileSync(new URL("../../../pages/BoardPage/components/card/checklist/BoardCardChecklist.tsx", import.meta.url), "utf8");
    const dependencies = Object.fromEntries([...source.matchAll(/from "([^"]+)"/g)].map((match) => [match[1], {}]));
    Object.assign(dependencies, {
        "react/jsx-runtime": { jsx: noop, jsxs: noop },
        react: {
            memo: (value: unknown) => value,
            useRef: () => ({ current: {} }),
            useState: () => [{ type: "idle" }, noop],
            useEffect: (effect: () => unknown) => effects.push(effect),
        },
        "@/core/providers/BoardCardProvider": { useBoardCard: () => ({ viewportRef: { current: viewport }, hasRoleAction: () => true }) },
        "@/core/models/roles": { ProjectRole: { EAction: { CardUpdate: "update" } } },
        "@/core/utils/ComponentUtils": { cn: noop },
        "@/core/helpers/dnd/createDndColumnEvents": { COLUMN_IDLE: { type: "idle" } },
        "@/core/helpers/dnd": { columnRowDndHelpers: { column: (props: { scrollable?: unknown }) => registrations.push(props) } },
        "tiny-invariant": { __esModule: true, default: assert },
    });
    const component = load("../../../pages/BoardPage/components/card/checklist/BoardCardChecklist.tsx", dependencies).default;
    component({ checklist: { useField: () => 0 }, checkitemsMap: {} });
    effects.forEach((effect) => effect());
    assert.equal(registrations.length, 1);
    assert.equal(registrations[0].scrollable, undefined);
});

test("column drag and drop registration does not require a private scroll container", () => {
    let scrolling = 0;
    let dragging = 0;
    let dropping = 0;
    const dependencies = {
        "@atlaskit/pragmatic-drag-and-drop/element/adapter": {
            draggable: () => {
                dragging++;
                return noop;
            },
            dropTargetForElements: () => {
                dropping++;
                return noop;
            },
        },
        "@atlaskit/pragmatic-drag-and-drop/combine": { combine: (...cleanups: (() => void)[]) => () => cleanups.forEach((cleanup) => cleanup()) },
        "@atlaskit/pragmatic-drag-and-drop/element/preserve-offset-on-source": {},
        "@atlaskit/pragmatic-drag-and-drop/element/set-custom-native-drag-preview": {},
        "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge": {},
        "@langboard/core/utils": {},
        "tiny-invariant": { __esModule: true, default: assert },
        "@/core/helpers/dnd/createDndColumnRowDataHelper": { __esModule: true, default: () => ({}) },
        "@/core/helpers/dnd/auto-scroll/entry-point/element": {
            autoScrollForElements: () => {
                scrolling++;
                return noop;
            },
        },
        "@/core/helpers/dnd/auto-scroll/entry-point/unsafe-overflow/element": {
            unsafeOverflowAutoScrollForElements: () => {
                scrolling++;
                return noop;
            },
        },
    };
    const create = load("./createDndColumnEvents.ts", dependencies).default;
    const props = { column: {}, symbolSet: {}, draggable: {}, dropTarget: {}, settings: {}, setState: noop, renderPreview: noop };
    const cleanup = create(props) as () => void;
    assert.equal(dragging, 1);
    assert.equal(dropping, 1);
    assert.equal(scrolling, 0);
    cleanup();
    const privateCleanup = create({ ...props, scrollable: viewport }) as () => void;
    assert.equal(scrolling, 2);
    privateCleanup();
});
