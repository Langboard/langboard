import assert from "node:assert/strict";
import { test } from "node:test";
import { assertResourceSelectionCurrent, confirmResourceWrite, readResourceCandidates, type ResourceSnapshot } from "./ResourceSelection.ts";

const candidate = { resource_type: "workflow", external_resource_id: "erp:workflow:example", label: "Example" };
const snapshot = (): ResourceSnapshot => ({
    app_revision: "a".repeat(64),
    binding_uid: "binding",
    binding_revision: "b".repeat(64),
    resource_types: ["workflow"],
    items: [{ ...candidate, access_revision: 3, selected: false }],
});

test("thirteen application definitions remain suggestions without authority fields", () => {
    const input = { candidates: Array.from({ length: 13 }, (_, i) => ({ ...candidate, external_resource_id: `erp:${i}:${"x".repeat(70)}` })) };
    assert.deepEqual(readResourceCandidates(input), input.candidates);
    assert.notEqual(readResourceCandidates(input)[0], input.candidates[0]);
});

test("unknown fields cannot smuggle board, connection, selected state or grants", () => {
    for (const field of ["project_uid", "connection_uid", "selected", "capabilities", "binding_revision"]) {
        assert.throws(() => readResourceCandidates({ candidates: [candidate], [field]: "forged" }));
        assert.throws(() => readResourceCandidates({ candidates: [{ ...candidate, [field]: "forged" }] }));
    }
});

test("candidate bounds, empty identities, duplicates and control characters are rejected", () => {
    for (const candidates of [
        [],
        Array(21).fill(candidate),
        [candidate, { ...candidate, label: "Other" }],
        [null],
        [{ ...candidate, external_resource_id: "x".repeat(201) }],
        [{ ...candidate, resource_type: "x".repeat(65) }],
        [{ ...candidate, label: "x".repeat(161) }],
        [{ ...candidate, label: "" }],
        [{ ...candidate, external_resource_id: " padded " }],
        [{ ...candidate, label: "line\nbreak" }],
    ])
        assert.throws(() => readResourceCandidates({ candidates }));
});

test("confirmation accepts unchanged native authority and does not mutate its snapshot", () => {
    const shown = snapshot(),
        original = structuredClone(shown);
    assertResourceSelectionCurrent(shown, structuredClone(shown), candidate, shown);
    assert.deepEqual(shown, original);
});

test("app, board binding, type and panel revision changes reject confirmation", () => {
    const shown = snapshot();
    for (const change of [{ app_revision: "new" }, { binding_uid: "other" }, { binding_revision: "new" }, { resource_types: [] }])
        assert.throws(() => assertResourceSelectionCurrent(shown, { ...snapshot(), ...change }, candidate, shown));
    assert.throws(() => assertResourceSelectionCurrent(shown, snapshot(), candidate, { ...shown, app_revision: "old-panel" }));
});

test("concurrent grant, revoke or deletion requires a new human review", () => {
    const shown = snapshot();
    for (const items of [[], [{ ...shown.items[0], access_revision: 4 }], [{ ...shown.items[0], selected: true }]])
        assert.throws(() => assertResourceSelectionCurrent(shown, { ...snapshot(), items }, candidate, shown));
    assert.throws(() => assertResourceSelectionCurrent({ ...snapshot(), items: [] }, snapshot(), candidate));
});

test("cancel before confirmation or during revalidation performs no write", async () => {
    for (const alreadyAborted of [true, false]) {
        const controller = new AbortController(),
            state = { busy: false, needsRefresh: false };
        let writes = 0;
        if (alreadyAborted) controller.abort();
        await assert.rejects(
            confirmResourceWrite(
                state,
                controller.signal,
                async () => {
                    controller.abort();
                },
                async () => {
                    writes++;
                }
            )
        );
        assert.equal(writes, 0);
        assert.equal(state.busy, false);
    }
});

test("permission/revision denial never dispatches a consent write", async () => {
    const state = { busy: false, needsRefresh: false };
    let writes = 0;
    await assert.rejects(
        confirmResourceWrite(
            state,
            new AbortController().signal,
            async () => {
                throw new Error("403 or changed revision");
            },
            async () => {
                writes++;
            }
        )
    );
    assert.equal(writes, 0);
});

test("a double confirmation cannot dispatch while the first request is in flight", async () => {
    const state = { busy: false, needsRefresh: false },
        controller = new AbortController();
    const ready = Promise.withResolvers<void>();
    let writes = 0;
    const first = confirmResourceWrite(
        state,
        controller.signal,
        async () => {
            await ready.promise;
        },
        async () => {
            writes++;
        }
    );
    await assert.rejects(
        confirmResourceWrite(
            state,
            controller.signal,
            async () => {},
            async () => {
                writes++;
            }
        )
    );
    ready.resolve();
    await first;
    assert.equal(writes, 1);
});

test("an unknown write result stays latched until explicit fresh review", async () => {
    const state = { busy: false, needsRefresh: false },
        controller = new AbortController();
    let writes = 0;
    const send = async () => {
        writes++;
        throw new Error("response lost");
    };
    await assert.rejects(confirmResourceWrite(state, controller.signal, async () => {}, send));
    await assert.rejects(confirmResourceWrite(state, controller.signal, async () => {}, send));
    assert.equal(writes, 1);
    assert.equal(state.needsRefresh, true);
});

test("a board/session/permission generation change during a read discards confirmation", async () => {
    const state = { busy: false, needsRefresh: false },
        controller = new AbortController();
    const waiting = Promise.withResolvers<void>();
    let current = true,
        writes = 0;
    const operation = confirmResourceWrite(
        state,
        controller.signal,
        async () => {
            await waiting.promise;
        },
        async () => {
            writes++;
        },
        () => current
    );
    current = false;
    waiting.resolve();
    await assert.rejects(operation);
    assert.equal(writes, 0);
});

test("initial selection does not require a previously approved resource or inferred entity", async () => {
    const empty = { ...snapshot(), items: [] };
    let writes = 0;
    await confirmResourceWrite(
        { busy: false, needsRefresh: false },
        new AbortController().signal,
        async () => {
            assertResourceSelectionCurrent(empty, structuredClone(empty), candidate, empty);
        },
        async () => {
            writes++;
        }
    );
    assert.equal(writes, 1);
});

test("native settings can still revoke an existing resource after its type was removed", () => {
    const shown = { ...snapshot(), resource_types: [], items: [{ ...candidate, access_revision: 3, selected: true }] };
    assert.doesNotThrow(() => assertResourceSelectionCurrent(shown, structuredClone(shown), candidate, undefined, false));
    assert.throws(() => assertResourceSelectionCurrent(shown, structuredClone(shown), candidate, undefined, true));
});
