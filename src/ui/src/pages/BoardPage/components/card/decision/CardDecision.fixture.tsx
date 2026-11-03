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
import BoardCard from "../BoardCard";
import { BoardCardSectionSaveProvider } from "../BoardCardSectionSaveProvider";
import i18n from "@/i18n";
import "@/core/injection";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const now = new Date();
const user = AuthUser.Model.fromOne({ uid: "User0000001", firstname: "Ada", lastname: "Reader", created_at: now, updated_at: now });
useAuthStore.setState({ state: "loaded", pageLoaded: true, currentUser: user });
api.defaults.baseURL = location.origin;
const networkAdapter = api.defaults.adapter;
api.interceptors.request.use((config) => {
    if (
        config.url?.includes("/graph-approvals") ||
        config.url?.includes("/decisions") ||
        config.url?.includes("/decision-actors/") ||
        config.url?.includes("/decision-gate") ||
        config.url?.endsWith("/workflow-stages")
    ) {
        config.adapter = networkAdapter;
        return config;
    }
    config.adapter = async () => ({
        config,
        data: /\/card\/Card000000[12]$/.test(config.url ?? "")
            ? {
                  card: {
                      uid: config.url!.split("/").at(-1),
                      project_uid: "Board000001",
                      title: "Native Decision card",
                      created_at: now,
                      updated_at: now,
                      visibility: "PROJECT",
                      current_auth_role_actions: params.has("readonly") ? ["read"] : ["read", "card_update"],
                      count_comment: 0,
                      last_change_seq: 7,
                      description: { content: "" },
                      work_state: {
                          execution_generation: 0,
                          material_kind: "work",
                          workflow_stage: "ready",
                          checklist_progress: { total: 0, completed: 0 },
                          reasons: [],
                          state_inconsistency: [],
                      },
                  },
                  attachments: [],
                  checklists: [],
                  project_columns: [],
                  project_labels: [],
                  bot_scopes: [],
                  global_relationships: [],
              }
            : { records: [], items: [], next_cursor: null, comments: [] },
        status: 200,
        statusText: "OK",
        headers: {},
    });
    return config;
});
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    const authUser = useAuthStore((state) => state.currentUser);
    const [cardUID, setCardUID] = useState("Card0000001");
    return (
        <>
            <Dialog.Root open modal={false}>
                <Dialog.Content
                    withCloseButton={false}
                    disableMotionAnimation
                    nonModalOverlay
                    className="flex h-[calc(100dvh-80px)] w-[calc(100vw-24px)] max-w-5xl flex-col overflow-hidden p-0"
                >
                    <button onClick={() => setCardUID(cardUID === "Card0000001" ? "Card0000002" : "Card0000001")}>Switch card</button>
                    <button onClick={() => useAuthStore.getState().removeToken()}>Expire session</button>
                    <output data-testid="auth-identity">{authUser?.uid ?? "signed out"}</output>
                    <BoardCardSectionSaveProvider>
                        <BoardCard projectUID="Board000001" cardUID={cardUID} currentUser={user} viewportRef={ref} />
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
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
                <DndProvider backend={HTML5Backend}>
                    <Fixture />
                </DndProvider>
            </QueryClientProvider>
        </MemoryRouter>
    </Suspense>
);
