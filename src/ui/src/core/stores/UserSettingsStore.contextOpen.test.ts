import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { getUserSettingsStore, useWorkbenchContextOpen } from "./UserSettingsStore.ts";

function contextSetter(userUID?: string) {
    let setOpen!: ReturnType<typeof useWorkbenchContextOpen>[1];
    function Context() {
        [, setOpen] = useWorkbenchContextOpen(userUID);
        return null;
    }
    renderToStaticMarkup(createElement(Context));
    return setOpen;
}

test("explicit open overrides both responsive and saved collapse for the current user", () => {
    const store = getUserSettingsStore();
    for (const collapsed of [undefined, true, false]) {
        store.replaceSettings({
            workbench_context_open: { alice: false, bob: false },
            workbench_sidebar_collapsed: { ...(collapsed === undefined ? {} : { alice: collapsed }), bob: true },
        });
        contextSetter("alice")(true);
        assert.deepEqual(getUserSettingsStore().settings.workbench_context_open, { alice: true, bob: false });
        assert.deepEqual(getUserSettingsStore().settings.workbench_sidebar_collapsed, { alice: false, bob: true });
    }
});

test("functional open uses current settings and close preserves collapse preference", () => {
    getUserSettingsStore().replaceSettings({ workbench_context_open: { alice: false } });
    const setOpen = contextSetter("alice");
    setOpen((previous) => !previous);
    assert.equal(getUserSettingsStore().settings.workbench_sidebar_collapsed?.alice, false);
    setOpen(false);
    assert.equal(getUserSettingsStore().settings.workbench_context_open?.alice, false);
    assert.equal(getUserSettingsStore().settings.workbench_sidebar_collapsed?.alice, false);
});

test("anonymous actions do not write another user's preferences", () => {
    getUserSettingsStore().replaceSettings({ workbench_sidebar_collapsed: { bob: true } });
    const before = getUserSettingsStore().settings;
    contextSetter()(true);
    assert.equal(getUserSettingsStore().settings, before);
});
