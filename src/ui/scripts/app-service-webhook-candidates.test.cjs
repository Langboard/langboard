const { test } = require("node:test");
const assert = require("node:assert/strict");
const { eligibleWebhookOrigin } = require("../src/pages/SettingsPage/appServiceWebhookCandidates.ts");

test("only trusted HTTPS app-event webhooks are eligible", () => {
    const trusted = ["https://app.example"];
    const candidate = { url: "https://app.example/hook", events: ["io.langboard.app.execution.requested.v1"] };
    assert.equal(eligibleWebhookOrigin(candidate, trusted), "https://app.example");
    for (const webhook of [
        { ...candidate, url: "https://fractalops.example/work.ready" },
        { ...candidate, url: "not a url" },
        { ...candidate, url: "https://user:pass@app.example/hook" },
        { ...candidate, url: "https://app.example/hook#fragment" },
        { ...candidate, url: "http://app.example/hook" },
        { ...candidate, events: ["work.ready"] },
    ]) assert.equal(eligibleWebhookOrigin(webhook, trusted), "");
    assert.equal(eligibleWebhookOrigin(candidate), "");
});
