import "@/core/injection";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useLocation } from "react-router";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import PersonalSignalInbox from "./PersonalSignalInbox";
import SignalInboxPanel from "../BoardPage/components/board/SignalInboxPanel";
import SignalPersonalInbox from "../BoardPage/components/board/SignalPersonalInbox";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const calls: { url?: string; method?: string; params: unknown }[] = [];
const scopeWrites: { expected_generation: number; event_kinds: string[]; project_uids: string[] }[] = [];
Object.assign(window, { inboxCalls: calls, inboxScopeWrites: scopeWrites });
const queryClient = new QueryClient();
const mode = params.get("summary") === "personal" ? "personal" : "shared";
const showSettings = params.has("revoke") || params.has("summary");
const destination = { kind: mode, destination_uid: "room", revision: 1, state: "verified", display_name: "Example room" };
const subscription = {
    scope: mode,
    subscription_uid: "subscription",
    destination_uid: "room",
    state: "enabled",
    generation: 1,
    event_kinds: mode === "personal" ? ["test", "card_activity", "missed_summary"] : ["test", "card_deadline"],
    project_uids: mode === "personal" ? [] : ["board2"],
};
const socket = { on: () => {}, off: () => {} };
const signal = {
    signal_uid: "signal2",
    project_uid: "board2",
    project_title: "Second board",
    resource_name: "second/repo",
    provider: "github",
    provider_name: "GitHub",
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
    if (showSettings) {
        const url = config.url ?? "";
        let data: unknown;
        if (url === "/board/board2/settings/apps")
            data = { apps: [{ key: "example", name: "Example app", app_revision: "revision", capabilities: ["events.receive"] }] };
        else if (url.endsWith("/inbound-connections"))
            data = { items: [{ connection_uid: "connection", state: "connected", revision: "revision" }], next_cursor: null };
        else if (url.endsWith("/notification-onboarding")) data = { available: false };
        else if (url.endsWith("/notification-settings"))
            data = { items: [config.params.section === "destinations" ? destination : subscription], next_cursor: null };
        else if (url.endsWith("/notification-subscriptions/subscription/scope")) {
            const body = JSON.parse(config.data);
            scopeWrites.push(body);
            const changed = JSON.stringify(subscription.event_kinds) !== JSON.stringify(body.event_kinds);
            Object.assign(subscription, {
                event_kinds: body.event_kinds,
                project_uids: body.project_uids,
                generation: subscription.generation + Number(changed),
            });
            data = subscription;
        } else if (url.endsWith("/notification-destinations/room/revoke")) {
            Object.assign(destination, { state: "revoked", revision: 2 });
            data = destination;
        } else if (url.endsWith("/notification-subscriptions/subscription/revoke")) {
            Object.assign(subscription, { state: "revoked", generation: 2 });
            data = subscription;
        } else if (url.endsWith("/service-template")) data = { can_prepare: false, template: { origin: "https://example.test" } };
        else data = {};
        return { config, status: 200, statusText: "OK", headers: {}, data };
    }
    if (params.has("delayed") && config.url?.startsWith("/dashboard")) await new Promise((resolve) => setTimeout(resolve, 400));
    if (config.url?.startsWith("/dashboard") && params.has("denied")) throw new Error("denied");
    let result: unknown = { items: [signal], next_cursor: null };
    if (config.url?.startsWith("/dashboard") && params.has("providers"))
        result = {
            items: [
                signal,
                {
                    ...signal,
                    signal_uid: "issue2",
                    provider: "glitchtip",
                    provider_name: "GlitchTip",
                    resource_name: "errors",
                    external_id: "issue7",
                    commit_sha: "",
                },
                {
                    ...signal,
                    signal_uid: "deploy2",
                    provider: "dokploy",
                    provider_name: "Dokploy",
                    resource_name: "application",
                    external_id: "deploy8",
                    commit_sha: "",
                },
            ],
            next_cursor: null,
        };
    if (config.url?.startsWith("/dashboard") && params.has("pages"))
        result = config.params?.after
            ? { items: [signal], next_cursor: null }
            : { items: [{ ...signal, signal_uid: "signal1", project_uid: "board1", project_title: "First board" }], next_cursor: "opaque" };
    return { config, status: 200, statusText: "OK", headers: {}, data: result };
};
function Fixture() {
    const location = useLocation();
    const [actorScope, setActorScope] = useState("first");
    if (showSettings)
        return (
            <QueryClientProvider client={queryClient}>
                <SignalPersonalInbox projectUID="board2" userUID="actor" />
            </QueryClientProvider>
        );
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
