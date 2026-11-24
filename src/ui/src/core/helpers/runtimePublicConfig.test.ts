import assert from "node:assert/strict";
import { test } from "node:test";
import { runtimePublicConfig } from "./runtimePublicConfig.ts";

const defaults = { API_URL: "https://api.example.invalid", PUBLIC_UI_URL: "https://ui.example.invalid", SOCKET_URL: "wss://socket.example.invalid" };

test("absent overrides preserve defaults and development ignores runtime input", () => {
    assert.deepEqual(runtimePublicConfig(defaults, {}, true), defaults);
    assert.equal(runtimePublicConfig(defaults, { API_URL: "invalid" }, false), defaults);
    assert.deepEqual(runtimePublicConfig(defaults, undefined, true), defaults);
});

test("explicit endpoints support paths, loopback and realtime protocols", () => {
    for (const protocol of ["http", "https", "ws", "wss"]) {
        const runtime = { API_URL: "http://localhost:5381/api", SOCKET_URL: `${protocol}://realtime.example.invalid/socket` };
        assert.deepEqual(runtimePublicConfig(defaults, runtime, true), { ...defaults, ...runtime });
    }
});

test("invalid supplied URLs fail closed without exposing their values", () => {
    for (const value of [
        "",
        "/relative",
        "https://",
        "https:/host",
        "https:////host",
        "https://host:99999",
        "javascript:alert(1)",
        "ws://host",
        "https://u:p@host",
        "https://@host",
        "https://host?",
        "https://host#",
        " https://host",
        "https://host\n",
        "https://ho\tst",
        "https://host/\"';alert(1)",
        "https://host/</script>",
        "https://host/\\path",
        null,
        1,
    ]) {
        assert.throws(() => runtimePublicConfig(defaults, { API_URL: value }, true), { message: "Invalid public runtime endpoint: API_URL" });
    }
    for (const value of [[], null, "", { invalid: true }]) {
        assert.throws(() => runtimePublicConfig(defaults, value, true));
    }
});
