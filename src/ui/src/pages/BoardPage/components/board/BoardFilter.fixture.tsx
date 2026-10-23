import "@/core/injection";
import { useCallback, useEffect, useRef, useState } from "react";
import Toast from "@/components/base/Toast";
import { BoardAddCardProvider } from "@/core/providers/BoardAddCardProvider";
import BoardColumnAddCard from "./BoardColumnAddCard";
import BoardColumnAddCardButton from "./BoardColumnAddCardButton";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectCard, ProjectColumn } from "@/core/models";
import { BoardProvider, useBoard } from "@/core/providers/BoardProvider";
import BoardFilter from "./BoardFilter";
import BoardFloatingNavigation from "./BoardFloatingNavigation";
import useBoardPanning from "./useBoardPanning";
import { ProjectRole } from "@/core/models/roles";
import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import useAuthStore from "@/core/stores/AuthStore";
import "@/i18n";
import "@/assets/styles/main.css";
const base = { created_at: new Date(), updated_at: new Date() };
const user = AuthUser.Model.fromOne({
    ...base,
    uid: "me",
    type: "user",
    email: "me@example.test",
    firstname: "Me",
    lastname: "User",
    username: "me",
    is_admin: true,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
});
const project = Project.Model.fromOne({
    ...base,
    uid: "fixture",
    title: "Fixture",
    all_members: [user],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: ["*"],
});
const otherUser = AuthUser.Model.fromOne({
    ...base,
    uid: "other",
    type: "user",
    email: "other@example.test",
    firstname: "Other",
    lastname: "User",
    username: "other",
    is_admin: true,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
});
const otherProject = Project.Model.fromOne({
    ...base,
    uid: "otherboard",
    title: "Other board",
    all_members: [user, otherUser],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: ["*"],
});
ProjectColumn.Model.fromArray(
    ["ready", "active"].map((uid, order) => ({
        ...base,
        uid,
        project_uid: "fixture",
        name: uid,
        order,
        is_archive: false,
        workflow_stage: `custom_${uid}`,
        count: 2,
    }))
);
const deadlineFixture = new URLSearchParams(location.search).has("deadline-fixture");
const deadlineCards = [
    ["today-future", "Today future", new Date(2026, 4, 14, 13)],
    ["today-elapsed", "Today elapsed", new Date(2026, 4, 14, 9)],
    ["week-start", "Week Monday", new Date(2026, 4, 11)],
    ["month-start", "Month start", new Date(2026, 4, 1)],
    ["quarter-start", "Quarter start", new Date(2026, 3, 1)],
    ["half-start", "Half start", new Date(2026, 0, 1)],
    ["year-end", "Year end", new Date(2026, 11, 31, 23, 59)],
    ["next-year", "Next year", new Date(2027, 0, 1)],
    ["none", "No deadline", null],
    ["completed", "Completed overdue", new Date(2026, 4, 14, 9)],
    ["reference", "Reference overdue", new Date(2026, 4, 14, 9)],
    ["suppressed", "Suppressed overdue", new Date(2026, 4, 14, 9)],
    ["archived", "Archived overdue", new Date(2026, 4, 14, 9)],
].map(([uid, title, deadline]) => ({
    uid: uid as string,
    title: title as string,
    deadline_at: deadline as Date | null,
    project_column_uid: uid === "reference" ? "reference" : "ready",
    completed: uid === "completed",
    archived_at: uid === "archived" ? new Date(2026, 4, 13) : undefined,
    work_state: uid === "suppressed" ? { overdue_suppressed: true, checklist_progress: { total: 0, completed: 0 } } : undefined,
    member_uids: uid === "today-future" ? ["me"] : [],
    creator: { uid: "me", type: "user", name: "Me User", avatar: null },
}));
if (deadlineFixture)
    ProjectColumn.Model.fromOne({
        ...base,
        uid: "reference",
        project_uid: "fixture",
        name: "Reference",
        order: 2,
        is_archive: false,
        workflow_stage: "reference",
    });
ProjectCard.Model.fromArray(
    (deadlineFixture
        ? deadlineCards
        : [
              {
                  uid: "a",
                  title: "Ready mine",
                  project_column_uid: "ready",
                  member_uids: ["me"],
                  creator: { uid: "me", type: "user", name: "Me User", avatar: null },
              },
              {
                  uid: "b",
                  title: "Active mine",
                  project_column_uid: "active",
                  member_uids: ["me"],
                  creator: { uid: "me", type: "user", name: "Me User", avatar: null },
              },
              {
                  uid: "c",
                  title: "Ready unassigned",
                  project_column_uid: "ready",
                  member_uids: [],
                  creator: { uid: "old", type: "user", name: "Former User", avatar: null },
              },
          ]
    ).map((card, order) => ({
        ...base,
        ...card,
        project_uid: "fixture",
        description: { content: "", type: "text" },
        labels: [],
        relationships:
            card.uid === "a"
                ? [{ ...base, uid: "ab", relationship_type_uid: "contains", parent_card_uid: "a", child_card_uid: "b" }]
                : card.uid === "b"
                  ? [
                        { ...base, uid: "ab", relationship_type_uid: "contains", parent_card_uid: "a", child_card_uid: "b" },
                        { ...base, uid: "bc", relationship_type_uid: "contains", parent_card_uid: "b", child_card_uid: "c" },
                    ]
                  : [{ ...base, uid: "bc", relationship_type_uid: "contains", parent_card_uid: "b", child_card_uid: "c" }],
        order,
    }))
);
function AddCardFixture() {
    const viewportRef = useRef<HTMLDivElement>(null);
    const panRef = useRef<HTMLDivElement>(null);
    const [, setParams] = useSearchParams();
    const { hasRoleAction } = useBoard();
    const compose = useCallback(() => {
        setParams(
            (params) => {
                params.set("new-card", "1");
                return params;
            },
            { replace: true }
        );
    }, [setParams]);
    useBoardPanning(panRef, hasRoleAction(ProjectRole.EAction.CardWrite) ? compose : undefined);
    return (
        <BoardAddCardProvider column={ProjectColumn.Model.getModel("ready")!} viewportRef={viewportRef} toLastPage={() => {}} isDefaultCardColumn>
            <div ref={panRef} data-testid="pan" className="h-48 w-full overflow-x-auto">
                <div className="h-full w-[1600px]">
                    <button data-block-board-panning="">Protected card</button>
                </div>
            </div>
            <div ref={viewportRef}>
                <BoardColumnAddCard />
                <BoardColumnAddCardButton />
            </div>
            <BoardFloatingNavigation project={project} currentUser={user} items={[]} dockEnabled />
            <Toast.Area />
        </BoardAddCardProvider>
    );
}
function Result() {
    const { cards, filterCard, filterCardMember, filterCardCreator, filterCardLabels, filterCardRelationships } = useBoard();
    const location = usePageLocation();
    if (new URLSearchParams(location.search).has("add-card")) return <AddCardFixture />;
    return (
        <>
            <BoardFilter />
            {deadlineFixture && (
                <button
                    onClick={() => {
                        ProjectCard.Model.getModel("today-future")!.deadline_at = new Date(2027, 0, 1);
                    }}
                >
                    Move deadline to next year
                </button>
            )}
            <p data-testid="url" className="break-all">
                {location.search}
            </p>
            <section aria-label="Results">
                {cards
                    .filter(
                        (card) =>
                            filterCard(card) &&
                            filterCardMember(card) &&
                            filterCardCreator(card) &&
                            filterCardLabels(card) &&
                            filterCardRelationships(card)
                    )
                    .map((card) => (
                        <p key={card.uid}>{card.title}</p>
                    ))}
            </section>
        </>
    );
}
function ScopeFixture() {
    const [actor, setActor] = useState(user);
    const [board, setBoard] = useState(project);
    useEffect(() => {
        useAuthStore.setState({ currentUser: actor, state: "loaded" });
    }, [actor]);
    return (
        <>
            <button onClick={() => setActor(actor === user ? otherUser : user)}>Switch account</button>
            <button onClick={() => setBoard(board === project ? otherProject : project)}>Switch board</button>
            <output data-testid="preference-scope">
                {actor.uid}:{board.uid}
            </output>
            <BoardProvider project={board} currentUser={actor}>
                <Result />
            </BoardProvider>
        </>
    );
}
if (new URLSearchParams(location.search).has("pull-denied")) {
    user.is_admin = false;
    project.current_auth_role_actions = [];
}
const router = createBrowserRouter([
    {
        path: "*",
        element: new URLSearchParams(location.search).has("deadline-panes") ? (
            <>
                <section aria-label="Left deadline pane">
                    <PageNavigationScope basePath="/board/fixture" initialEntry="/board/fixture?filters=deadlines:today">
                        <ScopeFixture />
                    </PageNavigationScope>
                </section>
                <section aria-label="Right deadline pane">
                    <PageNavigationScope basePath="/board/fixture" initialEntry="/board/fixture?filters=deadlines:none">
                        <ScopeFixture />
                    </PageNavigationScope>
                </section>
            </>
        ) : (
            <ScopeFixture />
        ),
    },
]);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
