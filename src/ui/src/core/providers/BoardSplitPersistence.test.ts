import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import { persistBoardSplit, restoreBoardSplit } from "./BoardSplitPersistence.ts";

function storage() {
    const data = new Map<string, string>();
    return { data, getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); }, removeItem: (key: string) => { data.delete(key); } };
}
const entries = ["/board/left/wiki/page?view=all#section", "/board/right/card"];

test("refresh restores two routes for the same user, with the actual address authoritative", () => {
    const s = storage();
    persistBoardSplit(s, "alice", entries, true);
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/right/wiki/new#anchor", true), [entries[0], "/board/right/wiki/new#anchor"]);
    assert.deepEqual(restoreBoardSplit(s, "bob", "/board/right", true), ["/board/right"]);
    assert.deepEqual(restoreBoardSplit(s, undefined, "/board/right", true), ["/board/right"]);
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/other", true), ["/board/other"]);
});

test("mobile restores one route without erasing the desktop workspace", () => {
    const s = storage();
    persistBoardSplit(s, "alice", entries, true);
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/right", false), ["/board/right"]);
    persistBoardSplit(s, "alice", ["/board/right"], false);
    assert.equal(restoreBoardSplit(s, "alice", "/board/right", true).length, 2);
});

test("closing a split removes restoration and malformed writes cannot persist", () => {
    const s = storage();
    for (const invalid of [["/board/right"], ["/board/left", "/board/left/card"], [...entries, "/board/third"], ["/board/right", "//evil/board/left"]]) {
        persistBoardSplit(s, "alice", entries, true);
        persistBoardSplit(s, "alice", invalid, true);
        assert.equal(s.data.size, 0);
    }
});

test("untrusted storage rejects corrupt, oversized, duplicate, unsafe and non-board routes", () => {
    const s = storage();
    persistBoardSplit(s, "alice", entries, true);
    const key = [...s.data.keys()][0];
    for (const raw of ["{", "null", "x".repeat(8193), ...[
        { version: 2, entries }, { version: 1, entries: [...entries, "/board/third"] },
        ...["/board/right/card", "https://evil.test/board/left", "/board/../left", "/board/left\\evil", "/dashboard", "/board/left\n", `/board/${"x".repeat(2049)}`].map((entry) => ({ version: 1, entries: [entry, "/board/right"] })),
    ].map((value) => JSON.stringify(value))]) {
        s.setItem(key, raw);
        assert.deepEqual(restoreBoardSplit(s, "alice", "/board/right", true), ["/board/right"], raw.slice(0, 100));
    }
});

test("denied or exhausted storage never prevents using the board", () => {
    const denied = { getItem: () => { throw Error("denied"); }, setItem: () => { throw Error("full"); }, removeItem: () => { throw Error("denied"); } };
    assert.deepEqual(restoreBoardSplit(denied, "alice", "/board/right", true), ["/board/right"]);
    assert.doesNotThrow(() => persistBoardSplit(denied, "alice", entries, true));
    assert.doesNotThrow(() => persistBoardSplit(denied, "alice", [], true));
});

// Execute the actual provider body with controlled hooks; no browser dependency tree or duplicated callbacks.
const source = readFileSync(new URL("./BoardSplitProvider.tsx", import.meta.url), "utf8");
const body = source.slice(source.indexOf("    const location = useLocation();"), source.indexOf("    return <BoardSplitContext.Provider"));
const run = new Function("scope", `with (scope) { ${stripTypeScriptTypes(body, { mode: "transform" })}; return value; }`);
function provider(s: ReturnType<typeof storage>, entry: string, desktop = true) {
    const slots: unknown[] = [];
    let cursor = 0;
    let effects: (() => void)[] = [];
    const events = new EventTarget();
    const navigations: string[] = [];
    const scope = {
        userUID: "alice", window: { sessionStorage: s, innerWidth: desktop ? 1440 : 390, dispatchEvent: events.dispatchEvent.bind(events) },
        ScreenMap: { size: { md: 768 } }, restoreBoardSplit, persistBoardSplit, CustomEvent, BOARD_PANE_BEFORE_CLOSE: "before-close",
        useLocation: () => ({ pathname: entry.split(/[?#]/)[0], search: "", hash: "" }), useNavigate: () => (target: string) => navigations.push(target),
        useState: (initial: unknown) => { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (next: unknown) => { slots[index] = typeof next === "function" ? next(slots[index]) : next; }]; },
        useRef: (initial: unknown) => { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
        useEffect: (effect: () => void) => effects.push(effect), useCallback: (fn: unknown) => fn, useMemo: (fn: () => unknown) => fn(),
    };
    const render = () => { cursor = 0; effects = []; const value = run(scope); effects.forEach((effect) => effect()); return value; };
    return { render, events, navigations };
}

test("production provider restores native focus, saves scoped navigation, limits panes, and preserves vetoed unsaved panes", () => {
    const s = storage();
    persistBoardSplit(s, "alice", entries, true);
    const h = provider(s, "/board/right");
    let split = h.render();
    assert.equal(split.panes.length, 2);
    assert.equal(split.focusedPane, "board-pane-1");
    split.open("third");
    assert.equal(h.render().panes.length, 2);
    split.rememberLocation("board-pane-0", "/board/left/wiki/updated#here");
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/right", true), ["/board/left/wiki/updated#here", "/board/right"]);
    const veto = (event: Event) => event.preventDefault();
    h.events.addEventListener("before-close", veto);
    split.close("board-pane-0");
    assert.equal(h.render().panes.length, 2);
    assert.equal(s.data.size, 1);
    assert.deepEqual(h.navigations, []);
    h.events.removeEventListener("before-close", veto);
    split.close("board-pane-0");
    split = h.render();
    assert.equal(split.panes.length, 1);
    assert.equal(split.focusedPane, "board-pane-1");
    assert.deepEqual(h.navigations, ["/board/right"]);
    assert.equal(s.data.size, 0);
});

test("production provider excludes mobile restoration and rejects mobile split opening", () => {
    const s = storage();
    persistBoardSplit(s, "alice", entries, true);
    const h = provider(s, "/board/right", false);
    const split = h.render();
    assert.equal(split.panes.length, 1);
    split.open("left");
    assert.equal(h.render().panes.length, 1);
    assert.equal(s.data.size, 1);
});

test("production provider persists a newly opened pair and allocates fresh IDs after restored close", () => {
    const s = storage();
    const h = provider(s, "/board/left");
    let split = h.render();
    split.open("right");
    split = h.render();
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/left", true), ["/board/left", "/board/right"]);
    const reloaded = provider(s, "/board/left");
    split = reloaded.render();
    split.close("board-pane-1");
    split = reloaded.render();
    split.open("third");
    split = reloaded.render();
    assert.deepEqual(split.panes.map((pane: { id: string }) => pane.id), ["board-pane-0", "board-pane-2"]);
    assert.deepEqual(restoreBoardSplit(s, "alice", "/board/left", true), ["/board/left", "/board/third"]);
});
