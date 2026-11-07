import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

const source = stripTypeScriptTypes(
    readFileSync(process.env.USER_SETTINGS_STORE_TEST_SOURCE ?? new URL("./UserSettingsStore.ts", import.meta.url), "utf8")
)
    .replace(/^import .*;$/gm, "")
    .replace(/^export default .*;$/gm, "")
    .replace(/^export /gm, "");

test("real settings store keeps fixture writes separate from existing application preferences", () => {
    const original = JSON.stringify({ open_cards: { actor: [{ cardUID: "preserved", pinned: true }] }, graph_view_modes: { actor: "columns" } });
    const storage = new Map([["user-settings-store", original]]);
    const timers: (() => void)[] = [];
    const load = (pathname: string) => {
        const context = vm.createContext({
            location: { pathname },
            localStorage: { getItem: (key: string) => storage.get(key), setItem: (key: string, value: string) => storage.set(key, value) },
            setTimeout: (callback: () => void) => timers.push(callback),
            immer: (initializer: unknown) => initializer,
            create: (initializer: (set: (update: (value: unknown) => void) => void, get: () => unknown) => unknown) => {
                const state: unknown = initializer(
                    (update) => update(state),
                    () => state
                );
                return { getState: () => state };
            },
        });
        vm.runInContext(source, context);
        return (script: string) => {
            vm.runInContext(script, context);
            while (timers.length) timers.shift()!();
        };
    };
    const fixture = load("/src/pages/DashboardPage/components/explorer-recovery.fixture.html");
    fixture('getUserSettingsStore().updateSettingsByKey("open_cards", { fixture: [] })');
    assert.equal(storage.get("user-settings-store"), original);
    assert.deepEqual(JSON.parse(storage.get("fixture-user-settings-store")!), { open_cards: { fixture: [] } });
    const application = load("/board/real-board");
    application('getUserSettingsStore().updateSettingsByKey("notifications_time_range", "3d")');
    assert.deepEqual(JSON.parse(storage.get("user-settings-store")!), { ...JSON.parse(original), notifications_time_range: "3d" });
    assert.deepEqual(JSON.parse(storage.get("fixture-user-settings-store")!), { open_cards: { fixture: [] } });
});
