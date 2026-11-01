import assert from "node:assert/strict";
import test from "node:test";
import { currentMyWorkWorkspaceFilter, type MyWorkWorkspaceSelection } from "./MyWorkWorkspaceFilter.ts";

test("selection is explicit, reversible, and belongs only to its actor, identity and board pane", () => {
    const selection: MyWorkWorkspaceSelection = {
        actorUID: "actor",
        identityVersion: 3,
        projectUID: "board",
        filter: { projectUID: null, group: "today" },
    };
    assert.equal(currentMyWorkWorkspaceFilter(null, "actor", 3, "board"), undefined);
    assert.deepEqual(currentMyWorkWorkspaceFilter(selection, "actor", 3, "board"), { projectUID: null, group: "today" });
    assert.equal(currentMyWorkWorkspaceFilter(selection, "other", 3, "board"), undefined);
    assert.equal(currentMyWorkWorkspaceFilter(selection, undefined, 3, "board"), undefined);
    assert.equal(currentMyWorkWorkspaceFilter(selection, "actor", 4, "board"), undefined);
    assert.equal(currentMyWorkWorkspaceFilter(selection, "actor", 3, "other"), undefined);
});
