import "@/core/injection";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useLocation } from "react-router";
import { useState } from "react";
import { api } from "@/core/helpers/Api";
import PersonalSignalInbox from "./PersonalSignalInbox";
import SignalInboxPanel from "../BoardPage/components/board/SignalInboxPanel";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const calls: { url?: string; method?: string; params: unknown }[] = [];
Object.assign(window, { inboxCalls: calls });
const socket = { on: () => {}, off: () => {} };
const signal = {
    signal_uid: "signal2",
    project_uid: "board2",
    project_title: "Second board",
    resource_name: "second/repo",
    provider: "github",
    event_type: "check.completed",
    can_bind_card: true,
    resource_uid: "resource2",
    connection_uid: "connection2",
    external_id: "55",
    commit_sha: "a".repeat(40),
    outcome: "failure",
    conflict: false,
    occurred_at: "2026-10-08T00:00:00Z",
};
api.defaults.adapter = async (config) => {
    calls.push({ url: config.url, method: config.method, params: config.params });
    if (params.has("delayed") && config.url?.startsWith("/dashboard")) await new Promise((resolve) => setTimeout(resolve, 400));
    if (config.url?.startsWith("/dashboard") && params.has("denied")) throw new Error("denied");
    let result: unknown = { items: [signal], next_cursor: null };
    if (config.url?.startsWith("/dashboard") && params.has("pages"))
        result = config.params?.after
            ? { items: [signal], next_cursor: null }
            : { items: [{ ...signal, signal_uid: "signal1", project_uid: "board1", project_title: "First board" }], next_cursor: "opaque" };
    return { config, status: 200, statusText: "OK", headers: {}, data: result };
};
function Fixture() {
    const location = useLocation();
    const [actorScope, setActorScope] = useState("first");
    return (
        <main>
            <button onClick={() => setActorScope("second")}>Switch account</button>
            {location.pathname.startsWith("/board/") ? (
                <SignalInboxPanel
                    projectUID="board2"
                    socket={socket}
                    canEdit={!params.has("readonly")}
                    cards={[{ uid: "card", title: "Sample card" }]}
                    columns={[{ uid: "ready", name: "Ready" }]}
                />
            ) : actorScope === "first" ? (
                <PersonalSignalInbox key={actorScope} actorScope={actorScope} />
            ) : (
                <p>Account switched</p>
            )}
        </main>
    );
}
void i18n.changeLanguage(params.get("lang") ?? "en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <BrowserRouter>
            <Fixture />
        </BrowserRouter>
    )
);
