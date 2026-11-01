// Actual full-edit navigation, provider, sections and API controllers. The browser
// spec substitutes collaboration transport, rich editor, picker and flip tray.
import { useRef } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { AuthUser, ProjectCard } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { BoardCardProvider, useBoardCard } from "@/core/providers/BoardCardProvider";
import { BoardCardFloatingNav } from "./BoardCard";
import BoardCardDeadline from "./BoardCardDeadline";
import BoardCardTitle from "./BoardCardTitle";
import BoardCardDescription from "./BoardCardDescription";
import { BoardCardSectionSaveProvider } from "./BoardCardSectionSaveProvider";
import { flipDraftKey, useCardFlipDraftStore } from "./CardFlipDraftStore";
import useCardDraftStore, { buildDraftScopeKey } from "@/core/stores/CardDraftStore";
import Toast from "@/components/base/Toast";
import "@/core/injection/StringExtensions";
import "@/i18n";
import "@/assets/styles/main.css";

const user = AuthUser.Model.fromOne({ uid: "full-edit-user", created_at: new Date(), updated_at: new Date() });
const card = ProjectCard.Model.fromOne({
    uid: "full-edit-card",
    project_uid: "full-edit-project",
    title: "Original title",
    description: { content: "Original description" },
    current_auth_role_actions: [ProjectRole.EAction.CardUpdate],
    created_at: new Date(),
    updated_at: new Date(),
});
const key = flipDraftKey(user.uid, card.project_uid, card.uid);
useCardFlipDraftStore.getState().save(key, { deadline_at: location.search.includes("unchanged") ? "" : "2030-06-15T12:00:00.000Z" });
// Other reads are fixture-local. PUT requests use the real API adapter and are
// held/released by Playwright, without touching a server card.
const adapter = api.defaults.adapter;
api.interceptors.request.use((config) => {
    if (config.method === "get") {
        config.adapter = async () => ({ config, data: { comments: [] }, status: 200, statusText: "OK", headers: {} });
    } else {
        config.adapter = adapter;
    }
    return config;
});

function Content() {
    const { isCardEditing } = useBoardCard();
    const ref = useRef<HTMLDivElement>(null);
    const flipDraft = useCardFlipDraftStore((state) => state.drafts[key]);
    const bodyDraft = useCardDraftStore(
        (state) => state.cache[buildDraftScopeKey({ userUID: user.uid, projectUID: card.project_uid, kind: "card-edit", scopeUID: card.uid })]
    );
    return (
        <main style={{ padding: 24, paddingBottom: 100 }}>
            <output data-testid="mode">{isCardEditing ? "edit" : "view"}</output>
            <output data-testid="flip-draft">{JSON.stringify(flipDraft ?? null)}</output>
            <output data-testid="body-draft">{bodyDraft?.content ?? "empty"}</output>
            <BoardCardTitle useDialogTitle={false} />
            <BoardCardDeadline />
            <BoardCardDescription scrollParentRef={ref} />
            <BoardCardFloatingNav isExpanded sectionLoading={false} />
            <Toast.Area />
        </main>
    );
}
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    return (
        <BoardCardSectionSaveProvider>
            <BoardCardProvider projectUID={card.project_uid} card={card} currentUser={user} viewportRef={ref}>
                <Content />
            </BoardCardProvider>
        </BoardCardSectionSaveProvider>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
            <Fixture />
        </QueryClientProvider>
    </MemoryRouter>
);
