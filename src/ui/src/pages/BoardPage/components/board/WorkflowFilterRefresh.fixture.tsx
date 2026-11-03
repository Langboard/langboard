import { createRoot } from "react-dom/client";
import { useEffect } from "react";
import { ESocketTopic } from "@langboard/core/enums";
import { createBrowserRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectCard, ProjectColumn } from "@/core/models";
import useBoardColumnWorkflowStageChangedHandlers from "@/controllers/socket/board/column/useBoardColumnWorkflowStageChangedHandlers";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { installNativeSocketFixture } from "./wiki-summary-tests/nativeSocket.fixture";
import { BoardProvider, useBoard } from "@/core/providers/BoardProvider";
import "@/i18n";
import "@/assets/styles/main.css";
const parameters = new URLSearchParams(location.search);
const authoritative = parameters.get("authoritative") === "true";
if (authoritative) installNativeSocketFixture();
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
const column = ProjectColumn.Model.fromOne({
    ...base,
    uid: "queue",
    project_uid: "fixture",
    name: "Queue",
    order: 0,
    is_archive: false,
    workflow_stage: "ready",
    workflow_counts_as_completed: authoritative ? parameters.get("completed") === "true" : undefined,
    count: 0,
});
const count = Number(parameters.get("count") ?? 3);
ProjectCard.Model.fromArray(
    Array.from({ length: count }, (_, order) => ({
        ...base,
        uid: `card${order}`,
        title: `Task ${order}`,
        project_uid: "fixture",
        project_column_uid: column.uid,
        description: { content: "", type: "text" },
        labels: [],
        relationships: [],
        member_uids: [],
        order,
    }))
);
function Controls() {
    const socket = useSocketOutsideProvider();
    const changed = useBoardColumnWorkflowStageChangedHandlers({ projectUID: project.uid });
    useEffect(() => {
        if (!authoritative) return;
        socket.subscribe(ESocketTopic.Board, [project.uid]);
        const off = changed.on();
        return () => {
            off();
            socket.unsubscribe(ESocketTopic.Board, [project.uid]);
        };
    }, []);
    return (
        <>
            <button
                onClick={() => {
                    const completed = authoritative ? !column.workflow_counts_as_completed : undefined;
                    for (let index = 0; index < count; index++) {
                        const card = ProjectCard.Model.getModel(`card${index}`)!;
                        card.work_state = {
                            version: 1,
                            workflow_stage: column.workflow_stage ?? null,
                            completed: completed ?? !card.work_state?.completed,
                            verification_state: "unverified",
                            verification_source_change_seq: 0,
                            verification: null,
                            execution_state: null,
                            blocker_state: null,
                            material_kind: "work",
                            lifecycle: "active",
                            active_queue_eligible: null,
                            checklist_progress: { total: 0, completed: 0 },
                            reasons: [],
                            state_inconsistency: [],
                        };
                    }
                    if (authoritative) {
                        const emit = (
                            window as unknown as {
                                emitNativeSummaryEvent: (topic: ESocketTopic, uid: string, event: string, data: unknown) => Promise<void>;
                            }
                        ).emitNativeSummaryEvent;
                        void emit(ESocketTopic.Board, project.uid, `board:column:workflow-stage:changed:${project.uid}`, {
                            uid: column.uid,
                            workflow_stage: column.workflow_stage,
                            workflow_counts_as_completed: completed,
                        });
                    }
                }}
            >
                Toggle completion policy
            </button>
            <label>
                Stage
                <select
                    aria-label="Stage"
                    defaultValue="ready"
                    onChange={(event) => {
                        column.workflow_stage = event.target.value || null;
                    }}
                >
                    {["ready", "active", "closed", "reference", ""].map((stage) => (
                        <option key={stage} value={stage}>
                            {stage || "unclassified"}
                        </option>
                    ))}
                </select>
            </label>
        </>
    );
}
function Result() {
    const { cards, filterCard } = useBoard();
    return (
        <section aria-label="Results">
            {cards.filter(filterCard).map((card) => (
                <p key={card.uid} data-card-uid={card.uid} data-order={card.order}>
                    {card.title}
                </p>
            ))}
        </section>
    );
}
const router = createBrowserRouter([
    {
        path: "*",
        element: (
            <BoardProvider project={project} currentUser={user}>
                <Controls />
                <Result />
            </BoardProvider>
        ),
    },
]);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
