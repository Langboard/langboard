import assert from "node:assert/strict";
import { test } from "node:test";
import { getMcpToolStore } from "./McpToolStore";
import { getBotStatusStore } from "./BotStatusStore";

test("MCP upsert and deletion preserve previous snapshots", () => {
    const name = "snapshot-tool";
    const before = getMcpToolStore().tools;
    try {
        getMcpToolStore().upsertTool({ name, description: "First", input_schema: {} });
        const inserted = getMcpToolStore().tools;
        assert.equal(before[name], undefined);
        assert.equal(inserted[name]?.description, "First");
        getMcpToolStore().upsertTool({ name, description: "Updated", input_schema: {} });
        assert.equal(inserted[name]?.description, "First");
        const updated = getMcpToolStore().tools;
        getMcpToolStore().deleteTool(name);
        assert.equal(updated[name]?.description, "Updated");
        assert.equal(getMcpToolStore().tools[name], undefined);
    } finally {
        getMcpToolStore().replaceTools([]);
    }
});

test("repeated bot status events preserve snapshots and stop the matching bot", () => {
    getBotStatusStore().replaceMap({ card: {}, project_column: {} });
    const before = getBotStatusStore().botStatusMap;
    try {
        const status = { type: "card" as const, targetUID: "card", botUID: "one" };
        getBotStatusStore().updateBotStatus({ ...status, status: "running" });
        const started = getBotStatusStore().botStatusMap;
        assert.equal(before.card.card, undefined);
        getBotStatusStore().updateBotStatus({ ...status, status: "running" });
        assert.deepEqual(getBotStatusStore().botStatusMap.card.card, ["one"]);
        getBotStatusStore().updateBotStatus({ ...status, botUID: "two", status: "running" });
        getBotStatusStore().updateBotStatus({ ...status, status: "stopped" });
        assert.deepEqual(getBotStatusStore().botStatusMap.card.card, ["two"]);
        assert.deepEqual(started.card.card, ["one"]);
    } finally {
        getBotStatusStore().replaceMap({ card: {}, project_column: {} });
    }
});
