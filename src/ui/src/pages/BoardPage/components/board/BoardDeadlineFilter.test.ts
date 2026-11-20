import assert from "node:assert/strict";
import { test } from "node:test";
import { BOARD_DEADLINE_PRESETS, deadlineCalendarRange, matchesBoardDeadline } from "./BoardDeadlineFilter.ts";

const column = { uid: "ready", name: "Ready", order: 0, is_archive: false, workflow_stage: "active" };
const now = new Date(2026, 4, 14, 12);
const card = (deadline_at?: Date | null) => ({ project_column_uid: "ready", deadline_at });
for (const preset of BOARD_DEADLINE_PRESETS.filter((value) => value !== "none" && value !== "overdue")) {
    test(`${preset} uses inclusive local start and exclusive end`, () => {
        const [start, end] = deadlineCalendarRange(preset, now);
        assert.equal(start.getHours(), 0);
        assert.equal(end.getHours(), 0);
        for (const [date, expected] of [
            [new Date(+start - 1), false],
            [start, true],
            [new Date(+end - 1), true],
            [end, false],
        ] as const) {
            assert.equal(matchesBoardDeadline(card(date), column, [preset], now), expected);
        }
    });
}
test("Monday weeks cross year boundaries and Sunday remains in the previous week", () => {
    assert.deepEqual(deadlineCalendarRange("week", new Date(2027, 0, 3)), [new Date(2026, 11, 28), new Date(2027, 0, 4)]);
    assert.deepEqual(deadlineCalendarRange("week", new Date(2027, 0, 4)), [new Date(2027, 0, 4), new Date(2027, 0, 11)]);
});
test("quarters, Jan/Jul halves, leap days and year rollovers are calendar based", () => {
    assert.deepEqual(deadlineCalendarRange("quarter", new Date(2026, 11, 31)), [new Date(2026, 9, 1), new Date(2027, 0, 1)]);
    assert.deepEqual(deadlineCalendarRange("quarter", new Date(2026, 3, 1)), [new Date(2026, 3, 1), new Date(2026, 6, 1)]);
    assert.deepEqual(deadlineCalendarRange("half-year", new Date(2026, 5, 30)), [new Date(2026, 0, 1), new Date(2026, 6, 1)]);
    assert.deepEqual(deadlineCalendarRange("half-year", new Date(2026, 6, 1)), [new Date(2026, 6, 1), new Date(2027, 0, 1)]);
    assert.deepEqual(deadlineCalendarRange("today", new Date(2024, 1, 29)), [new Date(2024, 1, 29), new Date(2024, 2, 1)]);
});
test("DST local days can be 23 or 25 hours without losing midnight boundaries", () => {
    const previous = process.env.TZ;
    process.env.TZ = "America/New_York";
    try {
        for (const [month, day, hours] of [
            [2, 8, 23],
            [10, 1, 25],
        ]) {
            const current = new Date(2026, month, day, 12);
            const [start, end] = deadlineCalendarRange("today", current);
            assert.equal((+end - +start) / 3600000, hours);
            assert.equal(matchesBoardDeadline(card(new Date(+end - 1)), column, ["today"], current), true);
            assert.equal(matchesBoardDeadline(card(end), column, ["today"], current), false);
            assert.equal(start.getHours(), 0);
            assert.equal(end.getHours(), 0);
        }
    } finally {
        if (previous === undefined) delete process.env.TZ;
        else process.env.TZ = previous;
    }
});
test("date ranges retain finished/reference cards while overdue obeys warning and unfinished policies", () => {
    const elapsed = card(new Date(+now - 1));
    assert.equal(matchesBoardDeadline(card(now), column, ["overdue"], now), true);
    assert.equal(matchesBoardDeadline(card(new Date(+now + 1)), column, ["overdue"], now), false);
    const suppressedCards = [
        { ...elapsed, completed: true },
        { ...elapsed, archived_at: now },
        { ...elapsed, checklist_completed_count: 1, checklist_total_count: 1 },
        { ...elapsed, source_type: "project_wiki" },
        { ...elapsed, source_type: "project_card" },
        { ...elapsed, work_state: { completed: false, overdue_suppressed: true, checklist_progress: { total: 0, completed: 0 } } },
        { ...elapsed, work_state: { completed: true, checklist_progress: { total: 0, completed: 0 } } },
    ];
    for (const candidate of suppressedCards) {
        assert.equal(matchesBoardDeadline(candidate, column, ["overdue"], now), false);
        assert.equal(matchesBoardDeadline(candidate, column, ["today"], now), true);
    }
    for (const policy of [{ workflow_stage: "reference" }, { workflow_counts_as_completed: true }, { is_archive: true }]) {
        assert.equal(matchesBoardDeadline(elapsed, { ...column, ...policy }, ["overdue"], now), false);
        assert.equal(matchesBoardDeadline(elapsed, { ...column, ...policy }, ["today"], now), true);
    }
});
test("no deadline, unknown values and same-group OR fail closed", () => {
    assert.equal(matchesBoardDeadline(card(null), column, ["none"], now), true);
    assert.equal(matchesBoardDeadline(card(), column, ["none"], now), true);
    assert.equal(matchesBoardDeadline(card(new Date(NaN)), column, ["none"], now), false);
    assert.equal(matchesBoardDeadline(card(now), column, ["tomorrow"], now), false);
    assert.equal(matchesBoardDeadline(card(null), column, ["today", "none"], now), true);
    assert.equal(matchesBoardDeadline(card(now), column, ["none", "today"], now), true);
    assert.equal(matchesBoardDeadline(card(now), column, [], now), true);
});
