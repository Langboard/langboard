const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load() {
    const state = {};
    const registry = {};
    const enums = { ESocketTopic: { None: "none", Global: "global", Card: "card" }, NONE_TOPIC_ID: "none", GLOBAL_TOPIC_ID: "global" };
    const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/core/stores/socket", name), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText;
    vm.runInNewContext(compile("state.ts"), { exports: state, require: () => enums });
    vm.runInNewContext(compile("registry.ts"), { exports: registry, require: (name) => name.endsWith("/state") ? state : enums });
    return { registry, map: state.getSocketMap() };
}

function counted(record) {
    let enumerations = 0;
    return { proxy: new Proxy(record, { ownKeys(target) { enumerations++; return Reflect.ownKeys(target); } }), reads: () => enumerations };
}

test("removing one card listener retains sibling listeners and skips populated ancestors", () => {
    const { registry, map } = load();
    const callbacks = [() => {}, () => {}];
    const props = { topic: "card", topicId: "card-1", event: "updated", eventKey: "pane-1" };
    for (const callback of callbacks) registry.addEvent({ ...props, callback });
    registry.addEvent({ ...props, eventKey: "pane-2", callback: callbacks[0] });
    const eventMap = counted(map.subscriptions.card["card-1"]);
    const topicMap = counted(map.subscriptions.card);
    topicMap.proxy["card-1"] = eventMap.proxy;
    map.subscriptions.card = topicMap.proxy;
    registry.removeEvent({ ...props, callback: callbacks[0] });
    assert.equal(map.subscriptions.card["card-1"].updated["pane-1"][0], callbacks[1]);
    assert.equal(map.subscriptions.card["card-1"].updated["pane-2"][0], callbacks[0]);
    assert.equal(eventMap.reads(), 0);
    assert.equal(topicMap.reads(), 0);
});

test("removing a complete event retains another event without scanning other cards", () => {
    const { registry, map } = load();
    const callback = () => {};
    const props = { topic: "card", topicId: "card-1", event: "updated", eventKey: "pane" };
    for (let i = 0; i < 2000; i++) registry.addEvent({ ...props, topicId: `card-${i}`, callback });
    registry.addEvent({ ...props, event: "moved", callback });
    const topicMap = counted(map.subscriptions.card);
    map.subscriptions.card = topicMap.proxy;
    registry.removeEvent({ ...props, callback });
    assert.equal(map.subscriptions.card["card-1"].updated, undefined);
    assert.equal(map.subscriptions.card["card-1"].moved.pane[0], callback);
    assert.equal(topicMap.reads(), 0);
    assert.equal(Object.keys(map.subscriptions.card).length, 2000);
});

test("final listener removes empty event, card and topic while preserving other topics", () => {
    const { registry, map } = load();
    const callback = () => {};
    const props = { topic: "card", topicId: "card-1", event: "updated", eventKey: "pane" };
    registry.addEvent({ ...props, callback });
    registry.addEvent({ ...props, topic: "board", topicId: "board-1", callback });
    registry.removeEvent({ ...props, callback });
    assert.equal(map.subscriptions.card, undefined);
    assert.equal(map.subscriptions.board["board-1"].updated.pane[0], callback);
    registry.removeEvent({ ...props, callback });
    assert.equal(map.subscriptions.board["board-1"].updated.pane[0], callback);
});
