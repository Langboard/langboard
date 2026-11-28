import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { devtronTarget, secureDevtronInput, readDevtronAccounts, readDevtronResources } from "./DevtronSettings.ts";

test("native, installed Helm and external Helm keep distinct canonical identities", async () => {
    assert.deepEqual(await devtronTarget({ kind: "inventory", first: "", second: "" }), { external_id: null });
    assert.deepEqual(await devtronTarget({ kind: "native", first: "17", second: "4" }), {
        deployment_scope: { appId: 17, environmentId: 4 },
        external_id: "app-17-env-4",
    });
    assert.deepEqual(await devtronTarget({ kind: "installed", first: "3", second: "" }), {
        deployment_scope: { installedAppId: 3 },
        external_id: "helm-app-3",
    });
    const identity = "1|default|release";
    assert.deepEqual(await devtronTarget({ kind: "external", first: identity, second: "" }), {
        deployment_scope: { helmAppId: identity },
        helm_app_id: identity,
        external_id: "helm-release-" + createHash("sha256").update(identity).digest("hex"),
    });
});

for (const [first, second] of [
    ["0", "4"],
    ["17", ""],
    ["17.1", "4"],
    ["0017", "4"],
    ["1000000001", "4"],
]) {
    test(`invalid native identity cannot become a selection: ${first}/${second}`, async () => {
        await assert.rejects(devtronTarget({ kind: "native", first, second }));
    });
}
for (const first of ["1|default|release|other", "1|../default|release", "true|default|release", "1|default|release/other"]) {
    test(`invalid external Helm tuple is rejected: ${first}`, async () => {
        await assert.rejects(devtronTarget({ kind: "external", first, second: "" }));
    });
}

test("secure input must be the same-origin exact one-use URL without userinfo/query/fragment", () => {
    const input_uid = "x".repeat(43),
        origin = "https://ui.example.invalid";
    const good = origin + "/secret-input/" + input_uid;
    assert.equal(secureDevtronInput({ input_uid, input_url: good }, origin), good);
    for (const input_url of [
        good + "?token=x",
        good + "#x",
        good.replace("https://", "https://user:password@"),
        good.replace("ui.example", "foreign.example"),
        good + "/other",
        origin + "/secret-input/" + "y".repeat(43),
    ]) {
        assert.throws(() => secureDevtronInput({ input_uid, input_url }, origin));
    }
});

test("HTML/error-shaped metadata and foreign-shaped identities cannot drive management writes", () => {
    const accounts = {
        app_revision: "a".repeat(64),
        available: true,
        ownership_scopes: [null],
        binding: null,
        items: [
            {
                connection_uid: "connection1",
                revision: "c".repeat(64),
                ownership: "personal",
                state: "connected",
                instance_url: "https://devtron.example.invalid",
            },
        ],
        next_cursor: null,
    };
    assert.equal(readDevtronAccounts(accounts), accounts);
    assert.throws(() =>
        readDevtronAccounts({
            ...accounts,
            items: [{ ...accounts.items[0], connection_uid: "../other" }],
        })
    );
    const resources = {
        app_revision: "a".repeat(64),
        binding_uid: "binding0001",
        binding_revision: "b".repeat(64),
        identity_revision: "c".repeat(64),
        resource_types: ["application"],
        items: [],
        next_cursor: null,
    };
    assert.equal(readDevtronResources(resources), resources);
    assert.throws(() => readDevtronResources({ ...resources, identity_revision: "stale" }));
});
