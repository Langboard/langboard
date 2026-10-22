import assert from "node:assert/strict";
import test from "node:test";
import { latestLabelStates } from "./LabelCollaboration.ts";

test("latest peer wins per label while equal timestamps preserve the first full metadata", () => {
    const first = { value: { labelUID: "one", checked: true, updatedAt: 1 }, user: "first" };
    const latest = { value: { labelUID: "one", checked: false, updatedAt: 3 }, user: "second" };
    const tied = { value: { labelUID: "one", checked: true, updatedAt: 3 }, user: "third" };
    const other = { value: { labelUID: "two", checked: true, updatedAt: 2 }, user: "other" };
    const values = [first, latest, tied, first, other];
    const result = latestLabelStates(values);
    assert.equal(result.one, latest);
    assert.equal(result.two, other);
    assert.equal(values.length, 5);
    assert.equal(latest.value.checked, false);
});
test("malformed peer values do not break or enter the label projection", () => {
    const values = [
        null,
        false,
        "text",
        4,
        {},
        { labelUID: 1, checked: true, updatedAt: 1 },
        { labelUID: "one", checked: "true", updatedAt: 1 },
        { labelUID: "one", checked: true, updatedAt: "1" },
    ];
    assert.deepEqual(Object.keys(latestLabelStates(values.map((value) => ({ value })))), []);
});
test("object-prototype property names are treated as peer keys without exceptions or prototype mutation", () => {
    for (const labelUID of ["constructor", "__proto__", "toString"]) {
        const meta = { value: { labelUID, checked: true, updatedAt: 1 }, user: "peer" };
        const result = latestLabelStates([meta]);
        assert.equal(Object.getPrototypeOf(result), null);
        assert.equal(Object.hasOwn(result, labelUID), true);
        assert.equal(result[labelUID], meta);
    }
    assert.equal(Object.prototype.hasOwnProperty.call(Object.prototype, "checked"), false);
});
