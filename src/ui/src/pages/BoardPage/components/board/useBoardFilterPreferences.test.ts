import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const source = stripTypeScriptTypes(readFileSync(new URL("./useBoardFilterPreferences.ts", import.meta.url), "utf8"))
    .replace(/^import .*;$/gm, "")
    .replace("export default function", "function");

function mutation(response: unknown) {
    let execute: (input: unknown) => Promise<unknown>;
    vm.runInNewContext(`${source}\nuseBoardFilterPreferences('actor', 'board');`, {
        useRef: () => ({ current: false }),
        useQueryClient: () => ({}),
        useQuery: () => ({ data: { revision: 0, pinned_filters: [], saved_views: [] } }),
        useMutation: (options: { mutationFn: typeof execute }) => {
            execute = options.mutationFn;
            return {};
        },
        api: { put: async () => ({ data: response }) },
        BOARD_FILTER_KEYS: ["columns"],
        CARD_PRIORITY_VALUES: [],
        isBoardDeadlinePreset: () => false,
    });
    return execute!;
}

const input = {
    userUID: "actor",
    projectUID: "board",
    revision: 0,
    pins: [],
    views: [{ name: "Ready", filters: [{ key: "columns", value: "ready" }] }],
};

test("saved view confirmation accepts equivalent JSON field orders", async () => {
    await mutation({
        revision: 1,
        pinned_filters: [],
        saved_views: [{ filters: [{ value: "ready", key: "columns" }], name: "Ready" }],
    })(input);
});

test("saved view confirmation still rejects changed content and stale revisions", async () => {
    for (const response of [
        { revision: 0, pinned_filters: [], saved_views: input.views },
        { revision: 1, pinned_filters: [], saved_views: [] },
        { revision: 1, pinned_filters: [], saved_views: [{ name: "Other", filters: input.views[0].filters }] },
        { revision: 1, pinned_filters: [], saved_views: [{ name: "Ready", filters: [{ key: "columns", value: "other" }] }] },
    ]) {
        await assert.rejects(mutation(response)(input), /Filter preference write was not confirmed/);
    }
});
