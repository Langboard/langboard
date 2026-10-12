import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import CardAppInteractions, { CardInteraction } from "./CardAppInteractions";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const calls: { method?: string; data: unknown }[] = [];
Object.assign(window, { interactionCalls: calls });
let lost = params.has("lost");
const kind = params.has("reaction") ? "comment_reaction" : "choice_card";
const item: CardInteraction = {
    interaction_uid: "00000000001",
    source: { app_key: "example-agent", connection_uid: "00000000004" },
    revision: 1,
    state: "pending",
    response: null,
    request: {
        kind: "question",
        prompt: { en: "Choose next action", ko: "다음 작업 선택" },
        questions: [
            {
                id: "action",
                prompt: { en: "Action" },
                type: "choice_or_text",
                max_length: 2000,
                choices: [
                    { id: "review", label: { en: "Review" } },
                    { id: "retry", label: { en: "Retry" } },
                ],
            },
        ],
        presentation: { type: kind, ...(kind === "comment_reaction" ? { comment_uid: "00000000002", reactions: { "thumbs-up": "review" } } : {}) },
        expires_at: new Date(Date.now() + (params.has("expired") ? -1000 : 3600000)).toISOString(),
        execution: { request_uid: "00000000003", generation: 7 },
    },
};
if (params.has("approval")) {
    item.request.kind = "approval";
    item.request.presentation = { type: "card_action" };
    item.request.questions = [
        {
            id: "action",
            prompt: { en: "Approval" },
            type: "single_choice",
            choices: [
                { id: "approve", label: { en: "Approve", ko: "승인" } },
                { id: "reject", label: { en: "Reject", ko: "거절" } },
            ],
        },
    ];
}
if (params.has("free")) item.request.questions = [{ id: "action", prompt: { en: "Comment" }, type: "free_text", max_length: 2000 }];
if (params.has("multi"))
    item.request.questions = [
        {
            id: "action",
            prompt: { en: "Action" },
            type: "multi_choice",
            min_selections: 1,
            max_selections: 1,
            choices: item.request.questions[0].choices,
        },
    ];
if (params.has("move")) {
    item.state = "answered";
    item.revision = 2;
    item.response = { actor_uid: "00000000005", response_uid: "00000000006", values: { action: { choice_id: "review" } } };
    item.workflow_plan = { state: "planned", workflow_stage: "review" };
}
let runtimeState = params.has("stopped") ? "stopped" : "authorized";
let resumeRequest: string | null = null;
api.defaults.adapter = async (config) => {
    const data = config.data ? JSON.parse(config.data) : null;
    calls.push({ method: config.method, data });
    if (config.url?.includes("/apps/executions")) {
        if (config.method === "post") {
            if (config.url?.endsWith("/resume")) resumeRequest = "00000000009";
            else runtimeState = "stop_requested";
            if (lost) {
                lost = false;
                throw new Error("Stop response lost after commit");
            }
        }
        return {
            config,
            data: {
                next_cursor: params.has("runtimepages") && !config.params?.cursor ? "00000000003" : null,
                items: params.has("runtime")
                    ? [
                          {
                              request_uid: "00000000003",
                              app_key: "example-agent",
                              generation: config.params?.cursor ? 6 : 7,
                              state: runtimeState,
                              current_generation: params.has("stopped") ? 8 : 7,
                              resume_request_uid: resumeRequest,
                          },
                      ]
                    : [],
            },
            status: 200,
            statusText: "OK",
            headers: {},
        };
    }
    if (config.method === "post" && config.url?.endsWith("/transition")) {
        item.workflow_receipt = { state: "moved" };
        if (lost) {
            lost = false;
            throw new Error("Movement response lost");
        }
    } else if (config.method === "post") {
        item.state = "answered";
        item.revision = 2;
        item.response = { actor_uid: "00000000005", response_uid: "00000000006", values: data.values };
        if (lost) {
            lost = false;
            throw new Error("Response lost after commit");
        }
    }
    return {
        config,
        data: config.method === "get" ? { items: [structuredClone(item)], next_cursor: null, card_revision: 12 } : {},
        status: 200,
        statusText: "OK",
        headers: {},
    };
};
await i18n.changeLanguage(params.get("locale") ?? "en-US");
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <div className="mx-auto max-w-xl p-4">
            <CardAppInteractions projectUID="00000000003" cardUID="00000000004" userUID="00000000005" canRespond={!params.has("readonly")} />
        </div>
    </QueryClientProvider>
);
