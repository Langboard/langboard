const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
    notificationContextForReady,
    NOTIFICATION_CONNECT_READY,
    NOTIFICATION_CONNECT_CONTEXT,
} = require("../src/pages/BoardPage/components/board/notificationOnboarding.ts");
const origin = "https://receiver.example";
const popup = { closed: false };
const now = Date.parse("2026-11-18T08:00:00Z");
const context = {
    handoff_uid: "handoff0001",
    challenge_uid: "challenge01",
    challenge_token: "x".repeat(43),
    expires_at: "2026-11-18T08:10:00.000000+00:00",
};
const ready = { type: NOTIFICATION_CONNECT_READY, receiver_nonce: "12345678-1234-4234-8234-123456789abc" };
const event = (changes = {}) => ({ origin, source: popup, data: ready, ...changes });
test("only exact trusted popup and valid native context produce six-field message", () => {
    assert.deepEqual(notificationContextForReady(event(), popup, origin, context, now), {
        type: NOTIFICATION_CONNECT_CONTEXT,
        receiver_nonce: ready.receiver_nonce,
        ...context,
    });
    for (const bad of [
        event({ origin: "https://attacker.example" }),
        event({ source: { closed: false } }),
        event({ data: { ...ready, extra: "unexpected" } }),
        event({ data: { ...ready, receiver_nonce: "predictable" } }),
        event({ data: { ...ready, type: "legacy" } }),
    ])
        assert.equal(notificationContextForReady(bad, popup, origin, context, now), null);
    const closed = { closed: true };
    assert.equal(notificationContextForReady(event({ source: closed }), closed, origin, context, now), null);
});
test("native expiry and identifiers are mandatory; no URL or replacement lifetime", () => {
    for (const changes of [
        { expires_at: "2026-11-18T08:00:00Z" },
        { expires_at: "2026-11-18T08:10:00" },
        { expires_at: "invalid" },
        { challenge_token: "short" },
        { handoff_uid: "other/1" },
        { challenge_uid: "bad" },
    ])
        assert.equal(notificationContextForReady(event(), popup, origin, { ...context, ...changes }, now), null);
    assert.equal(notificationContextForReady(event(), popup, "http://receiver.example", context, now), null);
    assert.equal(notificationContextForReady(event(), popup, origin + "/path", context, now), null);
});
