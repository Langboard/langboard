import assert from "node:assert/strict";
import test from "node:test";
import { validateMyWorkPage, type MyWorkPageResult } from "./MyWorkContext.ts";

test("summary contract accepts authoritative totals beyond the page and rejects stale scope, invalid counts and oversized pages", () => {
    const card = {
        uid: "card",
        title: "Card",
        project_uid: "project",
        project_title: "Project",
        project_column_name: "Doing",
        updated_at: "2026-10-23T00:00:00Z",
        deadline_at: null,
        reasons: ["assigned"],
        group: "assigned" as const,
    };
    const page: MyWorkPageResult = {
        cards: [card],
        counts: { assigned: 61, today: 0, overdue: 0, upcoming: 0, review: 0, mentioned: 0, created: 0 },
        total: 61,
        next_cursor: "cursor",
        group: "assigned",
        project_uid: "project",
        timezone: "UTC",
        as_of: "2026-10-23T00:00:00Z",
        day_start: "2026-10-23T00:00:00Z",
        day_end: "2026-10-24T00:00:00Z",
        upcoming_end: "2026-10-31T00:00:00Z",
    };
    const filter = { projectUID: "project", group: "assigned" as const };
    assert.equal(validateMyWorkPage(page, filter), page);
    const priority = { ...page, order: "priority" as const };
    assert.equal(validateMyWorkPage(priority, filter, "priority"), priority);
    assert.throws(() => validateMyWorkPage(page, filter, "priority"), /Invalid My Work/);
    assert.throws(() => validateMyWorkPage(priority, filter), /Invalid My Work/);
    assert.throws(() => validateMyWorkPage({ ...page, order: "updated" }, filter, "priority"), /Invalid My Work/);
    for (const deadline_at of [null, "2026-10-25T23:15:00+09:00"]) {
        const valid = { ...page, cards: [{ ...card, deadline_at }] };
        assert.equal(validateMyWorkPage(valid, filter), valid);
    }
    for (const invalid of [
        ...[undefined, "", "invalid", 0, {}, "2026-99-25T00:00:00Z"].map(
            (deadline_at) =>
                ({
                    ...page,
                    cards: [{ ...card, deadline_at }],
                }) as unknown as MyWorkPageResult
        ),
        { ...page, project_uid: "prior-project" },
        { ...page, total: 1 },
        { ...page, cards: Array(51).fill(card) },
        { ...page, cards: [{ ...card, project_uid: "prior-project" }] },
        { ...page, counts: { ...page.counts, review: -1 } },
    ]) {
        assert.throws(() => validateMyWorkPage(invalid, filter), /Invalid My Work/);
    }
});
