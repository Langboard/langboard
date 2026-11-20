import assert from "node:assert/strict";
import { test } from "node:test";
import { applyOptimisticOrder } from "./applyOptimisticOrder.ts";

test("failed edge, end and single-list reorders restore original model orders", () => {
    for (const destination of [
        [1, 2, 0],
        [2, 0, 1],
        [2, 1, 0],
    ]) {
        const original = [{ order: 0 }, { order: 1 }, { order: 2 }];
        const reordered = destination.map((index) => original[index]);
        const { list, undo } = applyOptimisticOrder(original, reordered);
        assert.deepEqual(
            list.map((item) => item.order),
            [0, 1, 2]
        );
        assert.equal(undo(), original);
        assert.deepEqual(
            original.map((item) => item.order),
            [0, 1, 2]
        );
        undo();
        assert.deepEqual(
            original.map((item) => item.order),
            [0, 1, 2]
        );
    }
});

test("rollback preserves non-contiguous original orders without touching other fields", () => {
    const original = [
        { order: 3, column: "a" },
        { order: 8, column: "a" },
    ];
    const { list, undo } = applyOptimisticOrder(original, [original[1], original[0]]);
    assert.deepEqual(
        list.map((item) => item.order),
        [3, 8]
    );
    assert.equal(original[1].order, 3);
    undo();
    assert.deepEqual(original, [
        { order: 3, column: "a" },
        { order: 8, column: "a" },
    ]);
});

test("filtered reorder preserves visible manual slots and leaves an omitted row untouched", () => {
    const hidden = { uid: "hidden", order: 0 };
    const first = { uid: "first", order: 1 };
    const target = { uid: "target", order: 2 };
    const source = { uid: "source", order: 3 };
    const original = [first, target, source];
    const { list, undo } = applyOptimisticOrder(original, [first, source, target]);
    assert.deepEqual(
        list.map(({ uid, order }) => [uid, order]),
        [
            ["first", 1],
            ["source", 2],
            ["target", 3],
        ]
    );
    assert.equal(hidden.order, 0);
    undo();
    assert.deepEqual(
        original.map(({ uid, order }) => [uid, order]),
        [
            ["first", 1],
            ["target", 2],
            ["source", 3],
        ]
    );
});
