import { Suspense, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
import Dialog from "@/components/base/Dialog";
import BoardCard from "./BoardCard";
import { BoardCardSectionSaveProvider } from "./BoardCardSectionSaveProvider";
import i18n from "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";

const params = new URLSearchParams(location.search);
const calls: { url?: string; method?: string; data: unknown }[] = [];
Object.assign(window, { managerCalls: calls });
const now = new Date();
const user = AuthUser.Model.fromOne({ uid: "action-user", created_at: now, updated_at: now });
useAuthStore.setState({ state: "loaded", pageLoaded: true, currentUser: user });
let linked = true;
let revision = 3;
let selected = ["resource"];
let answered = false;
let runtimeState = "authorized";
api.defaults.adapter = async (config) => {
    const url = config.url ?? "";
    const data = config.data ? JSON.parse(config.data) : null;
    calls.push({ url, method: config.method, data });
    if (params.has("error") && config.method !== "get") throw new Error("Synthetic manager write failure");
    let result: unknown = { records: [], items: [], next_cursor: null };
    if (url.endsWith("/unlink")) {
        linked = false;
        revision++;
    } else if (config.method === "post" && url.endsWith("/signals")) {
        linked = true;
        revision++;
    } else if (config.method === "put") {
        selected = data.resource_uids;
        revision++;
    } else if (url.endsWith("/responses")) answered = true;
    else if (url.endsWith("/stop")) runtimeState = "stop_requested";
    if (/\/card\/action-(card|other)$/.test(url))
        result = {
            card: {
                uid: url.split("/").at(-1),
                project_uid: "action-project",
                title: "Native action card",
                created_at: now,
                updated_at: now,
                visibility: params.has("private") ? "PRIVATE" : "PROJECT",
                current_auth_role_actions: params.has("readonly") ? [] : ["update", "card_update"],
                count_comment: 0,
                last_change_seq: 7,
                description: { content: "" },
            },
        };
    else if (url.endsWith("/signals/resources"))
        result = { items: [{ uid: "resource", connection_uid: "connection", name: "example/repository" }], next_cursor: null };
    else if (url.includes("/settings/") && url.endsWith("/signals"))
        result = { items: [{ signal_uid: "signal", external_id: "55", commit_sha: "a".repeat(40), outcome: "success" }], next_cursor: null };
    else if (url.endsWith("/signals"))
        result = {
            source_change_seq: 7,
            bindings: linked ? [{ binding_uid: "binding", revision }] : [],
            items: linked
                ? [{ binding_uid: "binding", revision, state: "passed", resource_uid: "resource", external_id: "55", commit_sha: "a".repeat(40) }]
                : [],
        };
    else if (url.endsWith("/resource-context"))
        result = { items: [{ resource_uid: "resource", connection_uid: "connection", app_key: "sample", name: "Example issue" }], next_cursor: null };
    else if (url.endsWith("/resource-selections"))
        result = { items: [{ app_key: "sample", connection_uid: "connection", revision, resource_uids: selected }], next_cursor: null };
    else if (url.endsWith("/resources")) result = { revision, resource_uids: selected };
    else if (url.endsWith("/interactions"))
        result = {
            card_revision: 7,
            next_cursor: null,
            items: [
                {
                    interaction_uid: "request",
                    revision: answered ? 2 : 1,
                    state: answered ? "answered" : "pending",
                    source: { app_key: "sample", connection_uid: "connection" },
                    response: answered ? { values: { approval: { choice_id: "approve" } }, actor_uid: user.uid, response_uid: "response" } : null,
                    request: {
                        kind: "approval",
                        prompt: { en: "Approve pending change" },
                        expires_at: new Date(Date.now() + 3600000).toISOString(),
                        execution: { request_uid: "runtime", generation: 7 },
                        presentation: { type: "card_action" },
                        questions: [
                            {
                                id: "approval",
                                prompt: { en: "Approval" },
                                type: "single_choice",
                                choices: [{ id: "approve", label: { en: "Approve" } }],
                            },
                        ],
                    },
                },
            ],
        };
    else if (url.endsWith("/executions"))
        result = { items: [{ request_uid: "runtime", app_key: "sample", generation: 7, state: runtimeState }], next_cursor: null };
    return { config, data: result, status: 200, statusText: "OK", headers: {} };
};
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    const [cardUID, setCardUID] = useState("action-card");
    return (
        <>
            <button onClick={() => setCardUID("action-other")}>Switch identity</button>
            <Dialog.Root open modal={false}>
                <Dialog.Content
                    withCloseButton={false}
                    disableMotionAnimation
                    nonModalOverlay
                    className="flex h-[calc(100dvh-80px)] w-[calc(100vw-24px)] max-w-5xl flex-col overflow-hidden p-0"
                >
                    <BoardCardSectionSaveProvider>
                        <BoardCard projectUID="action-project" cardUID={cardUID} currentUser={user} viewportRef={ref} />
                    </BoardCardSectionSaveProvider>
                </Dialog.Content>
            </Dialog.Root>
        </>
    );
}
await i18n.changeLanguage("en-US");
createRoot(document.getElementById("root")!).render(
    <Suspense>
        <MemoryRouter>
            <QueryClientProvider client={new QueryClient()}>
                <DndProvider backend={HTML5Backend}>
                    <Fixture />
                </DndProvider>
            </QueryClientProvider>
        </MemoryRouter>
    </Suspense>
);
