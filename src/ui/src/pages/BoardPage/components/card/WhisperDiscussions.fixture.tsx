import { AxiosError } from "axios";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import WhisperDiscussions, { WhisperAnchor, WhisperThread } from "./WhisperDiscussions";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const calls: { method?: string; path?: string; data: unknown }[] = [];
Object.assign(window, { whisperCalls: calls });
let denied = false;
let lost = params.has("lost");
let nextID = 1;
const rows: (WhisperThread & { messages: { uid: string; author_uid: string; content: string; created_at: string; mention_uids: string[] }[] })[] = [];
const now = () => new Date().toISOString();
api.defaults.adapter = async (request) => {
    const body = typeof request.data === "string" ? JSON.parse(request.data) : request.data;
    const path = request.url ?? "";
    calls.push({ method: request.method, path, data: body });
    if (denied) throw new Error("Current access revoked");
    if (params.has("write-denied") && request.method !== "get")
        throw new AxiosError("Current access denied", "ERR_BAD_REQUEST", request, undefined, {
            data: {},
            status: 403,
            statusText: "Forbidden",
            headers: {},
            config: request,
        });
    let data: unknown;
    const match = path.match(/\/whispers\/([^/]+)(?:\/([^/]+))?$/);
    const thread = rows.find((row) => row.uid === match?.[1]);
    if (request.method === "get") {
        if (!match) data = { threads: rows.map(({ messages, ...row }) => ({ ...row, preview: messages[0].content })), next_cursor: null };
        else if (match[2] === "messages") data = { thread, messages: thread?.messages ?? [], next_cursor: null };
        else
            data = {
                participants: [{ user_uid: "alice", firstname: "Alice", lastname: "Example", last_read_message_uid: null, has_unread: true }],
                next_cursor: null,
            };
    } else if (request.method === "post" && (!match || match[2] === "messages")) {
        const message = { uid: `message${nextID++}`, author_uid: "alice", content: body.content, created_at: now(), mention_uids: body.mention_uids };
        if (!match) {
            const created = {
                uid: `thread${nextID++}`,
                state: "OPEN" as const,
                creator_uid: "alice",
                created_at: now(),
                has_unread: false,
                anchor: {
                    type: body.relationship_uid ? ("RELATIONSHIP" as const) : ("CARD" as const),
                    uid: body.relationship_uid || "card",
                    subanchor_type: body.subanchor_type || null,
                    subanchor_uid: body.subanchor_uid || null,
                },
                messages: [message],
            };
            rows.unshift(created);
            data = { thread: created, message };
        } else {
            thread!.messages.push(message);
            data = { message };
        }
        if (lost) {
            lost = false;
            throw new Error("Write response lost after commit");
        }
    } else if (request.method === "patch") {
        thread!.state = body.state;
        data = { thread };
    } else {
        if (thread && match?.[2] === "read") thread.has_unread = false;
        data = { read: true };
    }
    return { data, status: 200, statusText: "OK", headers: {}, config: request };
};
const anchors: WhisperAnchor[] = [
    { key: "card", label: "Card", form: {} },
    { key: "relationship:relation", label: "Relationship · Related task", form: { relationship_uid: "relation" } },
    ...(["content_block", "comment", "checklist", "attachment"] as const).map((kind) => ({
        key: `${kind}:anchor`,
        label: `${kind} · Context`,
        form: { subanchor_type: kind, subanchor_uid: "anchor" },
    })),
];
const queryClient = new QueryClient();
function Fixture() {
    const [user, setUser] = useState("alice");
    const [allowed, setAllowed] = useState(!params.has("private"));
    return (
        <QueryClientProvider client={queryClient}>
            <main className="mx-auto max-w-2xl space-y-4 p-4">
                <div className="flex flex-wrap gap-3">
                    <button
                        onClick={() => {
                            denied = true;
                        }}
                    >
                        Revoke access
                    </button>
                    <button onClick={() => setAllowed(false)}>Hide private card</button>
                    <button onClick={() => setUser("other")}>Switch account</button>
                </div>
                {allowed && (
                    <WhisperDiscussions
                        projectUID="project"
                        cardUID="card"
                        userUID={user}
                        anchors={anchors}
                        people={[
                            { uid: "alice", name: "Alice Example" },
                            { uid: "bob", name: "Bob Example" },
                            { uid: "external", name: "External Member", external: true },
                        ]}
                    />
                )}
            </main>
        </QueryClientProvider>
    );
}
void i18n.changeLanguage(params.get("locale") ?? "en-US").then(() => createRoot(document.getElementById("root")!).render(<Fixture />));
