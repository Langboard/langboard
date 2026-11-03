import assert from "node:assert/strict";
import { test } from "node:test";
import { getCardVisualSignals } from "./CardVisualSignals.ts";

const now = new Date("2026-10-31T12:00:00Z");
const overdue = new Date("2026-10-30T12:00:00Z");
const base = { now, checklist: { total: 2, completed: 1 }, deadlineAt: overdue, agentRunning: true };

test("one repeating signal wins by severity without hiding independent static state", () => {
    const critical = getCardVisualSignals({ ...base, priority: "P0" });
    assert.equal(critical.motion, "critical");
    assert.equal(critical.deadlineLevel, "overdue");
    assert.equal(critical.agentRunning, true);
    assert.equal(getCardVisualSignals({ ...base, priority: "P1" }).motion, "overdue");
    assert.equal(getCardVisualSignals({ ...base, deadlineAt: undefined, priority: "P1" }).motion, "emergency");
    assert.equal(getCardVisualSignals({ ...base, deadlineAt: undefined }).motion, "agent");
});

test("terminal, suppressed, dragging, offscreen and reduced-motion states stop repeating motion", () => {
    for (const extra of [
        { completed: true },
        { archivedAt: now },
        { checklist: { total: 2, completed: 2 } },
        { workState: { completed: true } },
        { workState: { overdue_suppressed: true } },
        { workState: { lifecycle: "archived" as const } },
        { dragging: true },
        { visible: false },
        { reducedMotion: true },
    ]) {
        const result = getCardVisualSignals({ ...base, priority: "P0", ...extra });
        assert.equal(result.motion, "none", JSON.stringify(extra));
        assert.equal(result.priority, "P0");
        assert.equal(result.agentRunning, true);
    }
});

test("default urgency remains normal independently of overdue state", () => {
    assert.equal(getCardVisualSignals(base).priority, "P2");
    assert.equal(getCardVisualSignals(base).motion, "overdue");
    assert.equal(getCardVisualSignals({ ...base, agentRunning: false, deadlineAt: undefined }).motion, "none");
});
