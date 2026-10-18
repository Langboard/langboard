import assert from "node:assert/strict";
import test from "node:test";
import process from "node:process";
import { loadConfigFromFile } from "vite";

const valid = {
    API_URL: "https://api.example.invalid/api",
    PUBLIC_UI_URL: "https://board.example.invalid",
    SOCKET_URL: "wss://realtime.example.invalid/socket",
};

async function config(overrides = {}, mode = "production") {
    const previous = Object.fromEntries(Object.keys(valid).map((key) => [key, process.env[key]]));
    Object.assign(process.env, valid, overrides);
    try {
        return await loadConfigFromFile({ command: "build", mode }, "vite.config.ts", process.cwd(), "silent");
    } finally {
        for (const [key, value] of Object.entries(previous)) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
    }
}

for (const [key, value] of [
    ["API_URL", "/api"],
    ["PUBLIC_UI_URL", "board.example.invalid"],
    ["API_URL", "ftp://api.example.invalid"],
    ["PUBLIC_UI_URL", "wss://board.example.invalid"],
    ["SOCKET_URL", "javascript:alert(1)"],
    ["SOCKET_URL", "https://user:password@realtime.example.invalid"],
    ["API_URL", "https://api.example.invalid?token=private"],
    ["PUBLIC_UI_URL", "https://board.example.invalid/#route"],
    ["SOCKET_URL", " https://realtime.example.invalid "],
    ["API_URL", ""],
]) {
    test(`production rejects invalid ${key}: ${value.replace(/password|private/g, "redacted")}`, async () => {
        await assert.rejects(
            config({ [key]: value }),
            (error) => error.message.startsWith(`${key} `) && !error.message.includes(value || "password")
        );
    });
}

test("production embeds valid environment URLs unchanged", async () => {
    for (const socket of ["https://realtime.example.invalid/prefix", "wss://realtime.example.invalid/socket", "ws://localhost:5690"]) {
        const result = await config({ SOCKET_URL: socket, API_URL: "http://localhost:5381" });
        assert.equal(JSON.parse(result.config.define["process.env.SOCKET_URL"]), socket);
        assert.equal(JSON.parse(result.config.define["process.env.API_URL"]), "http://localhost:5381");
    }
});

test("development retains native localhost endpoints", async () => {
    const result = await config({ API_URL: "/ignored-in-development", SOCKET_URL: "invalid" }, "development");
    assert.match(JSON.parse(result.config.define["process.env.SOCKET_URL"]), /^http:\/\/localhost:/);
});
