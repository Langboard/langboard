import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { ProjectCheckitem, ProjectChecklist } from "@/core/models";
import useGetCardDetails from "./useGetCardDetails";
import "@/i18n";

let reads = 0;
let conversions = 0;
let creations = 0;
let connected = 0;
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
    return { status: 200, statusText: "OK", headers: {}, config, data };
};
function Fixture() {
    const query = useGetCardDetails({ project_uid: "detail-board", card_uid: "detail-card" });
    const summary = query.data
        ? {
              reads,
              conversions,
              creations,
              connected,
              lists: query.data.checklists.map((list) => ({
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
        createElement("button", { onClick: () => void query.refetch(), disabled: query.isFetching }, "Refresh")
    );
}
createRoot(document.getElementById("root")!).render(createElement(QueryClientProvider, { client: new QueryClient() }, createElement(Fixture)));
