import { createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser, ProjectCard, ProjectCardAttachment, ProjectCheckitem, ProjectChecklist } from "@/core/models";
import useGetCardDetails, { CardDetailSection } from "./useGetCardDetails";
import "@/i18n";

const authorityMode = new URLSearchParams(location.search).get("authority");
const partial = new URLSearchParams(location.search).has("partial");
const calls: { card: string; sections?: string; aborted?: boolean }[] = [];
let release: (() => void) | undefined;
let deferNext = false;
if (partial)
    useAuthStore.setState({
        currentUser: AuthUser.Model.fromOne({ uid: "account-a", created_at: new Date(), updated_at: new Date() }),
        state: "loaded",
    });
let reads = 0;
let conversions = 0;
let creations = 0;
let connected = 0;
Object.assign(window, { detailFixtureCounts: () => [conversions, creations, connected] });
const convert = ProjectCheckitem.Model.convertModel;
ProjectCheckitem.Model.convertModel = (model) => {
    conversions++;
    return convert(model);
};
ProjectCheckitem.Model.subscribe(
    "CREATION",
    "detail-fixture",
    (models) => {
        creations += models.length;
    },
    () => true
);
ProjectChecklist.Model.subscribe(
    "CREATION",
    "detail-fixture",
    (models) => {
        connected += models.flatMap((model) => model.checkitems).length;
    },
    () => true
);
api.defaults.adapter = async (config) => {
    reads++;
    const requestedCard = config.url?.split("/").at(-1) ?? "detail-card";
    const account = useAuthStore.getState().currentUser?.uid;
    const call = { card: requestedCard, sections: config.params?.sections, aborted: false };
    calls.push(call);
    config.signal?.addEventListener?.("abort", () => {
        call.aborted = true;
    });
    if (deferNext) {
        deferNext = false;
        await new Promise<void>((resolve) => {
            release = resolve;
        });
    }
    const item = (uid: string, title: string, checklist_uid: string) => ({
        uid,
        title,
        checklist_uid,
        card_uid: "detail-card",
        status: "started",
        order: 0,
        is_checked: false,
        accumulated_seconds: 23,
        timer_started_at: "2026-10-11T00:00:00Z",
        user: { uid: "worker", firstname: "Worker", lastname: "One" },
        cardified_card: { uid: "child-card", project_uid: "detail-board", project_column_uid: "column", title: "Child", order: 0 },
    });
    const data = {
        card: { uid: "detail-card", project_uid: "detail-board", project_column_uid: "column", title: "Detail", order: 0 },
        checklists:
            reads === 1
                ? [
                      {
                          uid: "first-list",
                          card_uid: "detail-card",
                          title: "First",
                          order: 0,
                          checkitems: [item("second", "Second", "first-list"), item("first", "First", "first-list")],
                      },
                      { uid: "other-list", card_uid: "detail-card", title: "Other", order: 1, checkitems: [item("third", "Third", "other-list")] },
                      { uid: "empty-list", card_uid: "detail-card", title: "Empty", order: 2 },
                  ]
                : [
                      { uid: "first-list", card_uid: "detail-card", title: "First", order: 0, checkitems: [item("first", "Changed", "first-list")] },
                      { uid: "other-list", card_uid: "detail-card", title: "Other", order: 1, checkitems: [] },
                      { uid: "empty-list", card_uid: "detail-card", title: "Empty", order: 2 },
                  ],
        attachments: [],
        global_relationships: [],
        project_columns: [],
        project_labels: [],
        bot_scopes: [],
    };
    if (authorityMode) {
        Object.assign(
            data.card,
            reads === 1
                ? { current_auth_role_actions: ["*"], can_cross_board: true, is_check_card: true, completed: true }
                : authorityMode === "malformed"
                  ? { current_auth_role_actions: [7], can_cross_board: "true", is_check_card: "true", completed: "true" }
                  : {}
        );
    }
    if (partial) {
        const section = String(config.params?.sections ?? "");
        const selected = section.split(",");
        const payload: Record<string, unknown> = { card: { ...data.card, uid: requestedCard, title: `${account}:${requestedCard}` } };
        if (selected.includes("description"))
            (payload.card as Record<string, unknown>).description = { content: `Body:${account}:${requestedCard}`, type: "text" };
        for (const key of selected)
            if (key !== "description")
                payload[key] =
                    key === "checklists"
                        ? data.checklists
                        : key === "attachments"
                          ? [{ uid: `attachment-${requestedCard}`, card_uid: requestedCard, filename: "Evidence" }]
                          : [];
        return { status: 200, statusText: "OK", headers: {}, config, data: payload };
    }
    return { status: 200, statusText: "OK", headers: {}, config, data };
};
function Fixture() {
    const [cardUID, setCardUID] = useState("detail-card");
    const [sections, setSections] = useState<CardDetailSection[]>(["checklists"]);
    const query = useGetCardDetails({ project_uid: "detail-board", card_uid: cardUID, sections: partial ? sections : undefined });
    const summary = query.data
        ? {
              reads,
              authority: query.data.action_authority,
              frozenAuthority: Object.isFrozen(query.data.action_authority) && Object.isFrozen(query.data.action_authority?.role_actions),
              modelRoles: query.data.card.current_auth_role_actions,
              calls,
              title: query.data.card.title,
              retainedDescription: ProjectCard.Model.getModel("detail-card")?.description?.content,
              retainedAttachments: ProjectCardAttachment.Model.getModels((model) => model.card_uid === "detail-card").map((model) => model.uid),
              retainedChecklistItems: ProjectChecklist.Model.getModel("first-list")?.checkitems.map((item) => item.uid),
              conversions,
              creations,
              connected,
              lists: (query.data.checklists ?? []).map((list) => ({
                  uid: list.uid,
                  items: list.checkitems.map((item) => ({
                      uid: item.uid,
                      title: item.title,
                      worker: item.user?.uid,
                      child: item.cardified_card?.uid,
                      seconds: item.accumulated_seconds,
                      started: item.timer_started_at?.toISOString(),
                  })),
              })),
          }
        : { reads, error: query.error?.stack };
    return createElement(
        "main",
        null,
        createElement("pre", { "data-testid": "summary" }, JSON.stringify(summary)),
        createElement("button", { onClick: () => void query.refetch(), disabled: query.isFetching }, "Refresh"),
        createElement("button", { onClick: () => setSections(["description", "attachments"]) }, "History"),
        createElement("button", { onClick: () => setSections(["execution_receipts"]) }, "Receipts"),
        createElement(
            "button",
            {
                onClick: () => {
                    deferNext = true;
                    void query.refetch();
                },
            },
            "Deferred refresh"
        ),
        createElement("button", { onClick: () => release?.() }, "Release"),
        createElement("button", { onClick: () => setCardUID("other-card") }, "Switch card"),
        createElement(
            "button",
            {
                onClick: () =>
                    useAuthStore.setState({
                        currentUser: AuthUser.Model.fromOne({ uid: "account-b", created_at: new Date(), updated_at: new Date() }),
                    }),
            },
            "Switch account"
        )
    );
}
createRoot(document.getElementById("root")!).render(createElement(QueryClientProvider, { client: new QueryClient() }, createElement(Fixture)));
