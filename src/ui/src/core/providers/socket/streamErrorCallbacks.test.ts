import assert from "node:assert/strict";
import { test } from "node:test";
import { ESocketTopic, GLOBAL_TOPIC_ID, NONE_TOPIC_ID } from "@langboard/core/enums";
import { getTopicWithId, resolveSocketTopicId } from "@/core/stores/socket/state";
import { removeStreamErrorCallback, runStreamErrorCallbacks, setStreamErrorCallback } from "./streamErrorCallbacks";

test("scoped socket topics require an ID while implicit topics keep their native IDs", () => {
    assert.equal(resolveSocketTopicId(ESocketTopic.None), NONE_TOPIC_ID);
    assert.equal(resolveSocketTopicId(ESocketTopic.Global), GLOBAL_TOPIC_ID);
    assert.equal(resolveSocketTopicId(ESocketTopic.Board, "board-uid"), "board-uid");
    assert.throws(() => getTopicWithId({ topic: ESocketTopic.Board }), /requires a topic ID/);
    assert.deepEqual(getTopicWithId({}), { topic: ESocketTopic.None, topicId: NONE_TOPIC_ID });
});

test("stream error callbacks survive repeated errors and independent unsubscription", async () => {
    const calls: string[] = [];
    const first = () => {
        calls.push("first");
    };
    const second = () => {
        calls.push("second");
    };

    setStreamErrorCallback(ESocketTopic.Board, "board:chat:stream", "first", first);
    setStreamErrorCallback(ESocketTopic.Board, "board:chat:stream", "second", second);
    try {
        await runStreamErrorCallbacks(new Event("error"));
        await runStreamErrorCallbacks(new Event("error"));
        assert.deepEqual(calls, ["first", "second", "first", "second"]);

        removeStreamErrorCallback(ESocketTopic.Board, "board:chat:stream", "first");
        await runStreamErrorCallbacks(new Event("error"));
        assert.deepEqual(calls, ["first", "second", "first", "second", "second"]);
    } finally {
        removeStreamErrorCallback(ESocketTopic.Board, "board:chat:stream", "first");
        removeStreamErrorCallback(ESocketTopic.Board, "board:chat:stream", "second");
    }
});
