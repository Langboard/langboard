import assert from "node:assert/strict";
import test from "node:test";
import { sortColumnCards, type TColumnCardSort } from "./columnCardSort.ts";
const day = (n: number) => new Date(`2026-09-${String(n).padStart(2, "0")}T00:00:00Z`);
const cards = [
    { uid: "a", order: 0, created_at: day(1), updated_at: day(3), member_uids: ["user"] },
    { uid: "b", order: 1, created_at: day(3), updated_at: day(1), deadline_at: day(4), member_uids: [] },
    { uid: "c", order: 2, created_at: day(2), updated_at: day(2), deadline_at: day(3), member_uids: ["user"] },
];
test("each sort criterion preserves source order and model identity", () => {
    const expected: Record<TColumnCardSort, string> = {
        manual: "abc",
        created_newest: "bca",
        created_oldest: "acb",
        updated_newest: "acb",
        updated_oldest: "bca",
        deadline: "cba",
        unassigned: "bac",
        priority: "abc",
    };
    for (const [mode, order] of Object.entries(expected)) {
        const result = sortColumnCards(cards, mode as TColumnCardSort);
        assert.equal(result.map((card) => card.uid).join(""), order);
        result.forEach((card) =>
            assert.equal(
                card,
                cards.find((original) => original.uid === card.uid)
            )
        );
    }
    assert.deepEqual(
        cards.map((card) => card.order),
        [0, 1, 2]
    );
    assert.equal(cards.map((card) => card.uid).join(""), "abc");
});
test("native priority sorts P0 first, defaults absent values to P2 and preserves manual ties", () => {
    const urgent = [
        { ...cards[0], priority: "P3" as const },
        { ...cards[1], priority: "P0" as const },
        cards[2],
        { ...cards[0], uid: "d", order: 4, priority: "P2" as const },
    ];
    assert.equal(
        sortColumnCards(urgent, "priority")
            .map((card) => card.uid)
            .join(""),
        "bcda"
    );
    assert.equal(
        sortColumnCards(urgent, "manual")
            .map((card) => card.uid)
            .join(""),
        "abcd"
    );
});
test("missing deadlines sort last, ties preserve manual order and changed metadata reorders", () => {
    const same = cards.map((card) => ({ ...card, deadline_at: undefined }));
    assert.equal(
        sortColumnCards(same.reverse(), "deadline")
            .map((card) => card.uid)
            .join(""),
        "abc"
    );
    const changed = cards.map((card) => (card.uid === "b" ? { ...card, updated_at: day(5), member_uids: ["user"] } : card));
    assert.equal(sortColumnCards(changed, "updated_newest")[0].uid, "b");
    assert.equal(
        sortColumnCards(changed, "unassigned")
            .map((card) => card.uid)
            .join(""),
        "abc"
    );
});
