import assert from "node:assert/strict";
import { test } from "node:test";
import { BOARD_WORK_VIEWS, matchesBoardWorkView } from "./BoardWorkView.ts";

const actor = "reader";
const card = {
    creator: { uid: actor, type: "user" as const, name: "Reader", avatar: null, created_at: "" },
    member_uids: [actor],
    work_state: { version: 1, lifecycle: "active", material_kind: "work", completed: false, workflow_stage: "active", blocker_state: null },
} as const;

test("customer views remain restricted to the current request creator", () => {
    for (const view of BOARD_WORK_VIEWS.customer) {
        assert.equal(matchesBoardWorkView({ ...card, member_uids: [actor] }, "another-user", view), false);
    }
    assert.equal(matchesBoardWorkView(card, actor, "requested"), true);
    assert.equal(matchesBoardWorkView(card, actor, "processing"), true);
    const waiting = { ...card, work_state: { ...card.work_state, workflow_stage: "waiting_customer_reply" } };
    assert.equal(matchesBoardWorkView(waiting, actor, "reply"), true);
    assert.equal(matchesBoardWorkView(waiting, actor, "processing"), false);
    const completed = { ...card, work_state: { ...card.work_state, workflow_stage: "closed", completed: true } };
    assert.equal(matchesBoardWorkView(completed, actor, "completed"), true);
    assert.equal(matchesBoardWorkView(completed, actor, "reply"), false);
});

test("staff views use assignment and native work state", () => {
    const waiting = { ...card, work_state: { ...card.work_state, workflow_stage: "waiting_customer_reply" } };
    const review = { ...card, work_state: { ...card.work_state, workflow_stage: "review" } };
    const blocked = { ...card, work_state: { ...card.work_state, blocker_state: "blocked" as const } };
    assert.equal(matchesBoardWorkView(card, actor, "assigned"), true);
    assert.equal(matchesBoardWorkView(card, "another-user", "assigned"), false);
    assert.equal(matchesBoardWorkView(waiting, actor, "waiting"), true);
    assert.equal(matchesBoardWorkView(review, actor, "review"), true);
    assert.equal(matchesBoardWorkView(blocked, actor, "blocked"), true);
    assert.equal(matchesBoardWorkView(card, actor, "staff"), true);
    assert.equal(matchesBoardWorkView({ ...card, archived_at: new Date() }, actor, "staff"), false);
    assert.equal(matchesBoardWorkView({ ...card, work_state: undefined }, actor, "staff"), false);
});

test("customer requests exclude cards in a legacy archive column", () => {
    const archived = { ...card, work_state: { ...card.work_state, lifecycle: "archived" as const } };
    assert.equal(matchesBoardWorkView(archived, actor, "requested"), false);
    assert.equal(matchesBoardWorkView({ ...card, work_state: undefined }, actor, "requested"), true);
});
