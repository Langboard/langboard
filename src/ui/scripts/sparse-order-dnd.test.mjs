import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import process from "node:process";

// Exercise the real drop callbacks, edge calculation and reorder implementation.
// Only browser registration/scroll adapters are replaced in this Node test.
const result = await build({
    stdin: {
        contents: `export { default as root } from "./src/core/helpers/dnd/createDndRootEvents.ts";
            export { default as single } from "./src/core/helpers/dnd/createDndSingleRootEvents.ts";
            export { attachClosestEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";`,
        resolveDir: process.cwd(),
    },
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    tsconfig: "tsconfig.app.json",
    plugins: [
        {
            name: "browser-drag-registrations",
            setup(plugin) {
                plugin.onResolve({ filter: /^@atlaskit\/pragmatic-drag-and-drop\/element\/adapter$/ }, () => ({
                    path: "monitor",
                    namespace: "browser-adapter",
                }));
                plugin.onResolve({ filter: /^@\/core\/helpers\/dnd\/auto-scroll\// }, () => ({ path: "scroll", namespace: "browser-adapter" }));
                plugin.onLoad({ filter: /.*/, namespace: "browser-adapter" }, ({ path }) => ({
                    contents:
                        path === "monitor"
                            ? `export const monitorForElements = options => {
                        globalThis.__dropMonitors.push(options); return () => {};
                    };`
                            : `export const autoScrollForElements = () => () => {};
                       export const unsafeOverflowAutoScrollForElements = () => () => {};`,
                }));
            },
        },
    ],
});
const temporary = await mkdtemp(join(tmpdir(), "sparse-order-dnd-"));
let root, single, attachClosestEdge;
try {
    const bundle = join(temporary, "callbacks.mjs");
    await writeFile(bundle, result.outputFiles[0].contents);
    ({ root, single, attachClosestEdge } = await import(pathToFileURL(bundle).href));
} finally {
    await rm(temporary, { recursive: true, force: true });
}
const symbols = Object.fromEntries(["root", "column", "columnDroppable", "row", "rowDroppable"].map((key) => [key, Symbol(key)]));
const topEdge = (data) =>
    attachClosestEdge(data, {
        element: { getBoundingClientRect: () => ({ left: 0, right: 100, top: 0, bottom: 100 }) },
        input: { clientX: 50, clientY: 1 },
        allowedEdges: ["top", "bottom"],
    });
function createBoard(rows, columns = [{ uid: "home", order: 0 }]) {
    globalThis.__dropMonitors = [];
    const changes = [];
    const cleanup = root({
        rowsMap: Object.fromEntries(rows.map((row) => [row.uid, row])),
        columns,
        columnKeyInRow: "column",
        symbolSet: symbols,
        settings: {},
        scrollable: {},
        changeRowOrder: (change) => changes.push(change),
        changeColumnOrder: (change) => changes.push(change),
    });
    return { changes, monitors: [...globalThis.__dropMonitors], cleanup };
}
const rows = () => [
    { uid: "first", order: 1, column: "home" },
    { uid: "target", order: 2, column: "home" },
    { uid: "source", order: 3, column: "home" },
];
const drop = (monitor, source, data) => monitor.onDrop({ source: { data: source }, location: { current: { dropTargets: [{ data }] } } });

test("filtered same-column drop uses identity indices and preserves manual slots", () => {
    const list = rows();
    const board = createBoard(list);
    try {
        drop(board.monitors[0], { [symbols.row]: true, row: list[2] }, topEdge({ [symbols.rowDroppable]: true, row: list[1] }));
        assert.equal(board.changes.length, 1);
        assert.equal(board.changes[0].rowUID, "source");
        assert.equal(board.changes[0].order, 2);
        assert.deepEqual(
            list.map((row) => row.order),
            [1, 3, 2]
        );
        board.changes[0].undo();
        assert.deepEqual(
            list.map((row) => row.order),
            [1, 2, 3]
        );
    } finally {
        board.cleanup();
    }
});
test("dropping a filtered row at its column end preserves slots and exact rollback", () => {
    const list = rows();
    const board = createBoard(list);
    try {
        drop(board.monitors[0], { [symbols.row]: true, row: list[1] }, { [symbols.column]: true, column: { uid: "home" } });
        assert.equal(board.changes[0].rowUID, "target");
        assert.equal(board.changes[0].order, 3);
        assert.deepEqual(
            list.map((row) => row.order),
            [1, 3, 2]
        );
        board.changes[0].undo();
        assert.deepEqual(
            list.map((row) => row.order),
            [1, 2, 3]
        );
    } finally {
        board.cleanup();
    }
});
test("a stale source absent from the visible list performs no reorder", () => {
    const list = rows();
    const board = createBoard(list);
    try {
        drop(
            board.monitors[0],
            { [symbols.row]: true, row: { uid: "removed", column: "home", order: 7 } },
            topEdge({ [symbols.rowDroppable]: true, row: list[0] })
        );
        assert.deepEqual(board.changes, []);
        assert.deepEqual(
            list.map((row) => row.order),
            [1, 2, 3]
        );
    } finally {
        board.cleanup();
    }
});
test("column reorders resolve identity independently of sparse stored order", () => {
    const columns = [
        { uid: "a", order: 9 },
        { uid: "b", order: 12 },
    ];
    const board = createBoard([], columns);
    try {
        drop(board.monitors[1], { [symbols.column]: true, column: columns[1] }, { [symbols.columnDroppable]: true, column: columns[0] });
        assert.equal(board.changes[0].columnUID, "b");
        assert.equal(board.changes[0].order, 9);
        board.changes[0].undo();
        assert.deepEqual(
            columns.map((column) => column.order),
            [9, 12]
        );
    } finally {
        board.cleanup();
    }
});
test("single-list drops preserve sparse manual slots and restore exact order", () => {
    const list = [
        { uid: "a", order: 3 },
        { uid: "b", order: 8 },
        { uid: "c", order: 11 },
    ];
    globalThis.__dropMonitors = [];
    const changes = [];
    const cleanup = single({
        rowsMap: Object.fromEntries(list.map((row) => [row.uid, row])),
        symbolSet: symbols,
        changeOrder: (change) => changes.push(change),
    });
    try {
        drop(
            globalThis.__dropMonitors[0],
            { [symbols.root]: true, [symbols.row]: true, row: list[2] },
            topEdge({ [symbols.root]: true, [symbols.row]: true, row: list[0] })
        );
        assert.equal(changes[0].rowUID, "c");
        assert.equal(changes[0].order, 3);
        changes[0].undo();
        assert.deepEqual(
            list.map((row) => row.order),
            [3, 8, 11]
        );
    } finally {
        cleanup();
    }
});
