import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

// Exercise the production constructor against a snapshotting store boundary.
const source = readFileSync(new URL("./User.ts", import.meta.url), "utf8");
const constructor = source.slice(source.indexOf("    constructor("), source.indexOf("    public static", source.indexOf("    constructor(")));
const body = constructor.slice(0, constructor.indexOf("\n    }\n") + 7);
const js = stripTypeScriptTypes(`class User extends Base { ${body} #subscribeUser() {} }; User`, { mode: "transform" });
class SnapshotStore {
    constructor(model) { this.state = { ...model }; }
}
const role = { EAction: {} };
const utils = { Type: { isNullOrUndefined: (value) => value == null }, String: { convertSafeEnum: (_enum, value) => `normalized:${value}` } };
const User = new Function("Base", "Utils", "ApiKeyRole", "SettingRole", "McpRole", `return eval(${JSON.stringify(js)})`)(SnapshotStore, utils, role, role, role);

test("missing permissions are empty in the initialized user store", () => {
    const user = new User({ uid: "reader" });
    for (const key of ["api_key_role_actions", "setting_role_actions", "mcp_role_actions"]) assert.deepEqual(user.state[key], []);
    assert.equal(user.state.setting_role_actions.some((action) => action === "*"), false);
});

test("supplied permissions are normalized before the snapshot", () => {
    const user = new User({ api_key_role_actions: ["read"], setting_role_actions: ["group_read"], mcp_role_actions: ["write"] });
    assert.deepEqual(user.state.api_key_role_actions, ["normalized:read"]);
    assert.deepEqual(user.state.setting_role_actions, ["normalized:group_read"]);
    assert.deepEqual(user.state.mcp_role_actions, ["normalized:write"]);
});
