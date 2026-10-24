import assert from "node:assert/strict";
import { test } from "node:test";
import { dayRange, monthRange, parseLocal, validRange } from "./BoardScheduleRange.ts";

test("local Today bounds preserve DST and month/year rollover", () => {
    for (const [day, hours] of [
        ["2026-03-08", 23],
        ["2026-11-01", 25],
        ["2026-12-31", 24],
    ] as const) {
        const range = dayRange(parseLocal(day));
        assert.equal(range.start, day);
        assert.equal((parseLocal(range.end).getTime() - parseLocal(range.start).getTime()) / 3600000, hours);
    }
    assert.deepEqual(dayRange(parseLocal("2026-12-31")), { start: "2026-12-31", end: "2027-01-01" });
    assert.deepEqual(monthRange(parseLocal("2026-03-08")), { start: "2026-03-01", end: "2026-04-01" });
});
test("invalid and excessive ranges cannot produce requests", () => {
    for (const date of ["", "2026-02-30", "2026-13-01", "2026-1-01", "x"]) assert.equal(Number.isNaN(parseLocal(date).getTime()), true);
    assert.equal(validRange(parseLocal("2026-03-08"), parseLocal("2026-03-08")), false);
    assert.equal(validRange(parseLocal("2026-03-08"), parseLocal("2026-03-07")), false);
    assert.equal(validRange(parseLocal("2026-03-08"), parseLocal("2027-03-08")), false);
    assert.equal(validRange(parseLocal("2026-03-08"), parseLocal("2026-03-09")), true);
});
