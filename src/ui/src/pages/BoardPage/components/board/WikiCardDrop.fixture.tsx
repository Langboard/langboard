import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectColumn } from "@/core/models";
import { BoardProvider, useBoard } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import useCreateWikiLinkedCard from "@/controllers/api/wiki/useCreateWikiLinkedCard";
import { api } from "@/core/helpers/Api";
import BoardWikiSidebar from "./BoardWikiSidebar";
import WikiCardDrop from "./WikiCardDrop";
import { BOARD_CARD_FOCUS_EVENT, type IBoardCardFocusEventDetail } from "./BoardConstants";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

api.defaults.baseURL = location.origin;
const base = { created_at: new Date(), updated_at: new Date() };
const actor = AuthUser.Model.fromOne({
    ...base,
    uid: "wiki-drag-reader",
    type: "user",
    email: "reader@example.test",
    username: "reader",
    firstname: "Wiki",
    lastname: "Reader",
    is_admin: false,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
});
useAuthStore.setState({ state: "loaded", currentUser: actor, getSessionVersion: () => 1, getToken: () => "wiki-drag-fixture" });
const project = Project.Model.fromOne({
    ...base,
    uid: "wiki-drag-board",
    title: "Wiki drag board",
    all_members: [actor],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: ["*"],
});
const foreignProject = Project.Model.fromOne({
    ...base,
    uid: "other-board",
    title: "Other board",
    all_members: [actor],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: ["*"],
});
const columns = ["first", "second", "archive", "foreign"].map((uid, order) =>
    ProjectColumn.Model.fromOne({
        ...base,
        uid,
        project_uid: uid === "foreign" ? "other-board" : project.uid,
        name: uid,
        order,
        is_archive: uid === "archive",
        count: 0,
    })
);

function Fixture() {
    const { cards } = useBoard();
    const [focus, setFocus] = useState<IBoardCardFocusEventDetail>();
    const { mutateAsync: createLinkedCard } = useCreateWikiLinkedCard();
    useEffect(() => {
        const listener = (event: Event) => setFocus((event as CustomEvent<IBoardCardFocusEventDetail>).detail);
        document.addEventListener(BOARD_CARD_FOCUS_EVENT, listener);
        return () => document.removeEventListener(BOARD_CARD_FOCUS_EVENT, listener);
    }, []);
    return (
        <main className="p-4">
            <button
                className="btn"
                onClick={() => {
                    project.current_auth_role_actions = [];
                }}
            >
                Revoke write
            </button>
            <button
                className="btn"
                onClick={() => {
                    project.current_auth_role_actions = ["*"];
                }}
            >
                Restore write
            </button>
            <button className="btn" onClick={() => void createLinkedCard({ project_uid: project.uid, wiki_uid: "button-wiki" }).catch(() => {})}>
                Create without column
            </button>
            <button className="btn" onClick={() => useAuthStore.setState({ getSessionVersion: () => 2 })}>
                Change session
            </button>
            <output data-testid="focus">{JSON.stringify(focus ?? null)}</output>
            <output data-testid="cards">{JSON.stringify(cards.map((card) => ({ uid: card.uid, column: card.project_column_uid })))}</output>
            <section className="gap-4 md:flex">
                <div className="h-96 w-60" data-testid="foreign-source">
                    <BoardProvider project={foreignProject} currentUser={actor}>
                        <BoardWikiSidebar projectUID={foreignProject.uid} />
                    </BoardProvider>
                </div>
                <div className="h-96 w-60">
                    <BoardWikiSidebar projectUID={project.uid} />
                </div>
                {columns.map((column) => (
                    <WikiCardDrop key={column.uid} column={column}>
                        <section aria-label={`${column.uid} column`} className="h-96 w-40 border p-4">
                            {column.name}
                        </section>
                    </WikiCardDrop>
                ))}
            </section>
        </main>
    );
}
void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <MemoryRouter>
                <BoardProvider project={project} currentUser={actor}>
                    <Fixture />
                </BoardProvider>
            </MemoryRouter>
        </QueryClientProvider>
    )
);
