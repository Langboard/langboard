import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveReminderWallTime, toReminderWallTime } from "./reminderWallTime.ts";

test("the selected time zone controls the stored instant and the form readback", () => {
    const local = "2027-01-15T09:00";
    for (const [zone, utc] of [
        ["Asia/Seoul", "2027-01-15T00:00:00.000Z"],
        ["America/New_York", "2027-01-15T14:00:00.000Z"],
        ["Asia/Kathmandu", "2027-01-15T03:15:00.000Z"],
    ]) {
        assert.equal(resolveReminderWallTime(local, zone), utc);
        assert.equal(toReminderWallTime(utc, zone), local);
    }
});

test("a daylight saving gap is rejected and first fold is consistent in both hemispheres", () => {
    assert.equal(resolveReminderWallTime("2026-03-08T02:30", "America/New_York"), null);
    assert.equal(resolveReminderWallTime("2026-11-01T01:30", "America/New_York"), "2026-11-01T05:30:00.000Z");
    assert.equal(resolveReminderWallTime("2026-04-05T02:30", "Australia/Sydney"), "2026-04-04T15:30:00.000Z");
});

test("an unchanged stored value retains seconds and its original fold", () => {
    const stored = "2026-11-01T06:30:41.769Z";
    assert.equal(resolveReminderWallTime("2026-11-01T01:30", "America/New_York", stored), stored);
    assert.equal(resolveReminderWallTime("2026-11-01T01:31", "America/New_York", stored), "2026-11-01T05:31:00.000Z");
});
