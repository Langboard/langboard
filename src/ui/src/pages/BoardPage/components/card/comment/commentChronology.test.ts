import assert from "node:assert/strict";
import { test } from "node:test";
import { newestCommentsFirst } from "./commentChronology.ts";

test("editing an older comment does not move it ahead of later events", () => {
    const earlier = { uid: "old", created_at: new Date("2026-10-20T10:00:00Z"), updated_at: new Date("2026-10-21T10:00:00Z") };
    const later = { uid: "new", created_at: new Date("2026-10-20T11:00:00Z"), updated_at: new Date("2026-10-20T11:00:00Z") };
    const input = [earlier, later];
    assert.deepEqual(newestCommentsFirst(input), [later, earlier]);
    assert.deepEqual(input, [earlier, later]);
    assert.equal(newestCommentsFirst(input)[1], earlier);
});

test("equal event timestamps stay deterministic across different response order", () => {
    const created_at = new Date("2026-10-20T10:00:00Z");
    const a = { uid: "a", created_at },
        b = { uid: "b", created_at };
    assert.deepEqual(newestCommentsFirst([a, b]), newestCommentsFirst([b, a]));
    assert.deepEqual(
        newestCommentsFirst([a, b]).map((comment) => comment.uid),
        ["b", "a"]
    );
});
