import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Link, MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectCard, ProjectColumn } from "@/core/models";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { AuthProvider, useAuth } from "@/core/providers/AuthProvider";
import { BoardProvider } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { installNativeSocketFixture } from "./wiki-summary-tests/nativeSocket.fixture";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import useCardRelationshipsUpdatedHandlers from "@/controllers/socket/card/useCardRelationshipsUpdatedHandlers";
import { ESocketTopic } from "@langboard/core/enums";
import useColumnCardSort from "./useColumnCardSort";
import type { TRowOrderChangedResponse } from "@/controllers/socket/shared/useRowOrderChangedHandlers";
import type { ICardRelationshipsUpdatedRawResponse } from "@/controllers/socket/card/syncCardRelationships";
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
const otherColumn = ProjectColumn.Model.fromOne({
    ...base,
    uid: "other-column",
    project_uid: boardUID,
    name: "Other column",
    order: 1,
    is_archive: false,
    count: 0,
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
// Controlled wire; native runtime registry and handler converters receive the exact server event names.
installNativeSocketFixture();
useSocketOutsideProvider().subscribe(ESocketTopic.Board, [boardUID]);
useCardRelationshipsUpdatedHandlers({ projectUID: boardUID }).on();
const emit = (event: string, data: unknown) =>
    (
        window as unknown as {
            emitNativeSummaryEvent: (topic: ESocketTopic, topicID: string, event: string, data: unknown) => unknown;
        }
    ).emitNativeSummaryEvent(ESocketTopic.Board, boardUID, event, data);
Object.assign(window, {
    collapseUpdates: {
        order: (columnUID: string, data: TRowOrderChangedResponse) => emit(`board:card:order:changed:${columnUID}`, data),
        relationships: (data: ICardRelationshipsUpdatedRawResponse) => emit(`board:card:relationships:updated:${boardUID}`, data),
        snapshot: () =>
            ProjectCard.Model.getModels((card) => card.project_uid === boardUID).map((card) => ({
                uid: card.uid,
                order: card.order,
                column: card.project_column_uid,
                relationships: card.relationships.map((edge) => ({
                    uid: edge.uid,
                    parent: edge.parent_card_uid,
                    child: edge.child_card_uid,
                    semantic: edge.machine_semantic,
                })),
            })),
    },
});
function OtherColumnList() {
    const scrollableRef = useRef<HTMLDivElement>(null);
    const updateBoard = useCallback(() => {}, []);
    return (
        <div ref={scrollableRef} data-testid="other-scrollport" className="h-96 w-80 overflow-y-auto p-3">
            <BoardColumnCardList column={otherColumn} updateBoard={updateBoard} scrollableRef={scrollableRef} onCardCountChange={updateBoard} />
        </div>
    );
}
const interactions = { dragStarts: 0, dragEnds: 0 };
document.addEventListener("dragstart", () => {
    interactions.dragStarts++;
});
document.addEventListener("dragend", () => {
    interactions.dragEnds++;
});
Object.assign(window, { collapseInteractions: interactions });
function List() {
    const location = usePageLocation();
    const scrollableRef = useRef<HTMLDivElement>(null);
    const [count, setCount] = useState(0);
    const { mode, setMode } = useColumnCardSort(boardUID, column.uid);
    const updateBoard = useCallback(() => {}, []);
    const viewerInputRef = useRef<HTMLInputElement>(null);
    const isViewerOpen = location.pathname === `/board/${boardUID}/grandchild`;
    useEffect(() => {
        if (isViewerOpen) viewerInputRef.current?.focus();
    }, [isViewerOpen, location.key]);
    return (
        <>
            <Link to={`/board/${boardUID}/grandchild`}>Search grandchild result</Link>
            <Link to="/board/different/grandchild">Other project card result</Link>
            {isViewerOpen && <input ref={viewerInputRef} aria-label="Viewer focus sentinel" />}
            <output data-testid="location">{location.pathname + location.search}</output>
            <p data-testid="count">{count}</p>
            <select aria-label="Sort cards" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
                <option value="manual">Manual</option>
                <option value="updated_newest">Recently updated</option>
            </select>
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
            {params.has("updates") && <OtherColumnList />}
        </BoardProvider>
    );
}
await i18n.changeLanguage(params.get("lang") ?? "en-US");
const initialEntry = `/board/${boardUID}${params.has("route") ? "/grandchild" : ""}${params.has("filtered") ? "?filters=keyword%3Akeep" : ""}`;
createRoot(document.getElementById("root")!).render(
    <MemoryRouter initialEntries={[initialEntry]}>
        <PageNavigationScope initialEntry={initialEntry} basePath={`/board/${boardUID}`} syncNative>
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
                <AuthProvider>
                    <Fixture />
                </AuthProvider>
            </QueryClientProvider>
        </PageNavigationScope>
    </MemoryRouter>
);
