import assert from "node:assert/strict";
import { test } from "node:test";
import { readPanelConnection } from "./PanelConnectionReads.ts";

const permission = { app_revision: "a".repeat(64), binding_revision: "b".repeat(64), connection_reads: ["deployments.list"] };
const params = { connection_uid: "C1234567890", read_key: "deployments.list", input: { resource_uid: "R1234567890", access_revision: 7 } };

test("host pins revisions and preserves typed provider403 without invalidating the frame", async () => {
    const controller = new AbortController();
    const result = { ok: false, error: { code: "upstream_error", status: 403, request_id: "provider-receipt" } };
    let calls = 0;
    assert.deepEqual(await readPanelConnection(permission, params, controller.signal, () => true, async (body, signal) => {
        calls++;
        assert.deepEqual(body, { ...params, app_revision: permission.app_revision, binding_revision: permission.binding_revision });
        assert.equal(signal, controller.signal);
        return result;
    }), result);
    assert.equal(calls, 1);
});

test("old hosts, unadvertised keys and identity or endpoint injection stop before native dispatch", async () => {
    let calls = 0;
    const post = async () => { calls++; return {}; };
    const signal = new AbortController().signal;
    await assert.rejects(readPanelConnection({ ...permission, connection_reads: undefined }, params, signal, () => true, post));
    await assert.rejects(readPanelConnection(permission, { ...params, read_key: "deployments.create" }, signal, () => true, post));
    for (const key of ["app_key", "project_uid", "app_revision", "binding_revision", "url", "method", "headers", "token"]) {
        await assert.rejects(readPanelConnection(permission, { ...params, [key]: "forged" }, signal, () => true, post));
    }
    for (const value of [null, [], { ...params, connection_uid: "foreign" }, { ...params, input: [] }]) {
        await assert.rejects(readPanelConnection(permission, value, signal, () => true, post));
    }
    assert.equal(calls, 0);
});

test("cancelled or replaced frame cannot start a native request", async () => {
    const controller = new AbortController();
    let calls = 0;
    const post = async () => { calls++; return {}; };
    await assert.rejects(readPanelConnection(permission, params, controller.signal, () => false, post), /scope changed/);
    controller.abort();
    await assert.rejects(readPanelConnection(permission, params, controller.signal, () => true, post), /scope changed/);
    assert.equal(calls, 0);
});

test("inflight cancellation or scope replacement discards the response without a retry", async () => {
    for (const cancellation of [false, true]) {
        const controller = new AbortController();
        let current = true;
        let calls = 0;
        await assert.rejects(readPanelConnection(permission, params, controller.signal, () => current, async () => {
            calls++;
            if (cancellation) controller.abort();
            else current = false;
            return { ok: true, data: { items: [] } };
        }), /scope changed/);
        assert.equal(calls, 1);
    }
});

test("native authority denial is propagated once for the owning frame to invalidate", async () => {
    const denied = new Error("native403");
    let calls = 0;
    await assert.rejects(readPanelConnection(permission, params, new AbortController().signal, () => true, async () => {
        calls++;
        throw denied;
    }), (error) => error === denied);
    assert.equal(calls, 1);
});
