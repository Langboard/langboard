import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
import { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectCard, ProjectColumn } from "@/core/models";
import { AuthProvider, useAuth } from "@/core/providers/AuthProvider";
import { BoardProvider } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { BoardColumnCardList } from "./BoardColumn";
import { BOARD_CARD_FOCUS_EVENT, BOARD_CARD_LOCATION_EVENT } from "./BoardConstants";

// Local model/provider fixture. No authenticated server or canary session is represented.
const params = new URLSearchParams(location.search);
const boardUID = params.get("board") ?? "board";
const base = { created_at: new Date(), updated_at: new Date() };
const user = AuthUser.Model.fromOne({
    ...base,
    uid: params.get("user") ?? "user",
    type: "user",
    email: "fixture@example.test",
    firstname: "Fixture",
    lastname: "User",
    username: "fixture",
    is_admin: true,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
});
useAuthStore.setState({ currentUser: user, state: "loaded", pageLoaded: true });
const project = Project.Model.fromOne({
    ...base,
    uid: boardUID,
    title: "Collapse fixture",
    all_members: [user],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: ["*"],
});
const column = ProjectColumn.Model.fromOne({
    ...base,
    uid: "column",
    project_uid: boardUID,
    name: "Column",
    order: 0,
    is_archive: false,
    count: 6,
});
const edges = [
    ["root", "child"],
    ["child", "grandchild"],
    ["root", "sibling"],
    ["sibling", "cousin"],
];
ProjectCard.Model.fromArray(
    ["root", "child", "grandchild", "sibling", "cousin", "unrelated"].map((uid, order) => ({
        ...base,
        uid,
        project_uid: boardUID,
        project_column_uid: "column",
        title: uid,
        order,
        description: { content: ["grandchild", "cousin"].includes(uid) ? "" : "keep Parent body retained", type: "text" },
        has_description: true,
        labels: [],
        member_uids: [],
        relationships: edges
            .filter(([parent, child]) => parent === uid || child === uid)
            .map(([parent, child]) => ({
                uid: `${parent}-${child}`,
                parent_card_uid: parent,
                child_card_uid: child,
                machine_semantic: "contains",
            })),
    }))
);
function List() {
    const scrollableRef = useRef<HTMLDivElement>(null);
    const [count, setCount] = useState(0);
    const updateBoard = useCallback(() => {}, []);
    return (
        <>
            <p data-testid="count">{count}</p>
            <button
                onClick={() =>
                    document.dispatchEvent(
                        new CustomEvent(BOARD_CARD_FOCUS_EVENT, {
                            detail: { cardUID: "grandchild", columnUID: "column", focus: true },
                        })
                    )
                }
            >
                Focus grandchild
            </button>
            <button
                onClick={() =>
                    document.dispatchEvent(
                        new CustomEvent(BOARD_CARD_LOCATION_EVENT, {
                            detail: { cardUID: "grandchild", columnUID: "column", focus: true, onLocated: () => {} },
                        })
                    )
                }
            >
                Locate grandchild
            </button>
            <button
                onClick={() =>
                    document.dispatchEvent(
                        new CustomEvent(BOARD_CARD_LOCATION_EVENT, {
                            detail: { cardUID: "grandchild", columnUID: "column", onLocated: () => {} },
                        })
                    )
                }
            >
                Measure grandchild
            </button>
            <div ref={scrollableRef} data-testid="scrollport" className="h-96 w-80 overflow-y-auto p-3">
                <BoardColumnCardList column={column} updateBoard={updateBoard} scrollableRef={scrollableRef} onCardCountChange={setCount} />
            </div>
        </>
    );
}
function Fixture() {
    const { currentUser } = useAuth();
    return (
        <BoardProvider project={project} currentUser={currentUser!}>
            <List />
        </BoardProvider>
    );
}
await i18n.changeLanguage(params.get("lang") ?? "en-US");
createRoot(document.getElementById("root")!).render(
    <MemoryRouter initialEntries={[`/board/${boardUID}${params.has("filtered") ? "?filters=keyword%3Akeep" : ""}`]}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <AuthProvider>
                <Fixture />
            </AuthProvider>
        </QueryClientProvider>
    </MemoryRouter>
);
