import http from "node:http";
import { URL } from "node:url";
let calls = [];
let hold = false;
let fail = false;
let empty = false;
let pending = [];
let generation = 0;
let mcp = false;
const server = http.createServer((req, res) => {
    res.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:4187");
    res.setHeader("Access-Control-Allow-Credentials", "true");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type,Authorization");
    res.setHeader("Content-Type", "application/json");
    if (req.method === "OPTIONS") {
        res.writeHead(204);
        res.end();
        return;
    }
    const url = new URL(req.url, "http://127.0.0.1:5387");
    const send = (data, status = 200) => {
        res.writeHead(status);
        res.end(JSON.stringify(data));
    };
    if (url.pathname === "/control") {
        if (url.searchParams.has("reset")) {
            calls = [];
            hold = false;
            fail = false;
            empty = false;
            generation = 0;
            mcp = false;
            pending.forEach((send) => send());
            pending = [];
        }
        if (url.searchParams.has("mcp")) mcp = true;
        if (url.searchParams.has("hold")) hold = true;
        if (url.searchParams.has("fail")) fail = true;
        if (url.searchParams.has("empty")) empty = true;
        if (url.searchParams.has("release")) {
            hold = false;
            generation++;
            const responses = pending;
            pending = [];
            responses.forEach((send) => send());
        }
        send({ calls, pending: pending.length });
        return;
    }
    calls.push(url.pathname + url.search);
    const sourceGeneration = generation;
    const delayedStatus = hold ? (pending.length === 0 ? "failed" : "completed") : "accepted";
    const finish = () => {
        if (fail) {
            fail = false;
            send({ error: "fixture unavailable" }, 500);
            return;
        }
        const board = url.pathname.split("/")[2];
        const parts = url.pathname.split("/");
        const row = {
            uid: url.searchParams.has("before") ? "page-two" : `run-${board}-${sourceGeneration}`,
            source: mcp ? "mcp" : "chat",
            status: mcp ? "unknown" : delayedStatus,
            created_at: "2026-10-22T10:00:00Z",
        };
        if (parts.length === 7)
            send({
                item: {
                    ...row,
                    detail: mcp
                        ? { tool: "patch_card_description", request_id: "a".repeat(32), payload_available: false }
                        : { output_text: `PRIVATE DETAIL ${board} ${sourceGeneration}` },
                },
            });
        else send({ items: empty ? [] : [row], next_cursor: empty || url.searchParams.has("before") ? null : "chat:fixturepage" });
    };
    if (hold) pending.push(finish);
    else finish();
});
server.listen(5387, "127.0.0.1");
