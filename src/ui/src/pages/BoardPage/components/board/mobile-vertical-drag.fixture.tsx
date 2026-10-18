import { createRoot } from "react-dom/client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, Project, ProjectCard, ProjectColumn, ProjectLabel } from "@/core/models";
import { BoardProvider, useBoard } from "@/core/providers/BoardProvider";
import { getUserSettingsStore } from "@/core/stores/UserSettingsStore";
import { columnRowDndHelpers } from "@/core/helpers/dnd";
import BoardColumn from "./BoardColumn";
import useBoardTouchCardDnd from "./useBoardTouchCardDnd";
import { BOARD_DND_SETTINGS, BOARD_DND_SYMBOL_SET } from "./BoardConstants";
import "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const allowed = params.get("permission") !== "denied";
const mode = params.get("sort") ?? "manual";
const base = { created_at: new Date(), updated_at: new Date() };
const user = AuthUser.Model.fromOne({
    ...base,
    uid: "mobile-user",
    type: "user",
    email: "fixture@example.test",
    firstname: "Fixture",
    lastname: "User",
    username: "fixture",
    is_admin: allowed,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
});
const project = Project.Model.fromOne({
    ...base,
    uid: "mobile-project",
    title: "Mobile drag fixture",
    all_members: [user],
    labels: [],
    invited_member_uids: [],
    current_auth_role_actions: [],
});
const label = ProjectLabel.Model.fromOne({ ...base, uid: "mobile-label", project_uid: project.uid, name: "Visible label", color: "#777777" });
const columns = ProjectColumn.Model.fromArray(
    ["source", "destination", "empty", "automatic"].map((uid, order) => ({
        ...base,
        uid,
        project_uid: project.uid,
        name: uid,
        order,
        is_archive: false,
        count: 0,
    }))
);
const cards = ProjectCard.Model.fromArray(
    Array.from({ length: 48 }, (_, order) => ({
        ...base,
        uid: `mobile-card-${order}`,
        title: order === 0 ? "Long native title with visible labels and nested hierarchy on a narrow mobile column" : `Native card ${order}`,
        project_uid: project.uid,
        project_column_uid: order < 30 ? "source" : "destination",
        order: order < 30 ? order : order - 30,
        description: { content: "", type: "text" },
        labels: [label],
        relationships: [],
        member_uids: [],
    }))
);
getUserSettingsStore().updateSettingsByKey(
    "column_card_sorts",
    Object.fromEntries(columns.map((column) => [`undefined:${project.uid}:${column.uid}`, column.uid === "automatic" ? "created_newest" : mode]))
);
function NativeBoard() {
    const { canDragCards } = useBoard();
    const scrollableRef = useRef<HTMLDivElement>(null);
    const [receipts, setReceipts] = useState<unknown[]>([]);
    const [mounted, setMounted] = useState(true);
    const rowsMap = Object.fromEntries(cards.map((card) => [card.uid, card]));
    const changeRowOrder = useCallback(({ rowUID, order, parentUID }: { rowUID: string; order: number; parentUID?: string }) => {
        setReceipts((previous) => [...previous, { rowUID, order, parentUID }]);
    }, []);
    useBoardTouchCardDnd({ enabled: canDragCards && mounted, scrollableRef, columns, rowsMap, changeRowOrder });
    useEffect(() => {
        if (!scrollableRef.current) return;
        return columnRowDndHelpers.root({
            columns,
            rowsMap,
            columnKeyInRow: "project_column_uid",
            symbolSet: BOARD_DND_SYMBOL_SET,
            scrollable: scrollableRef.current,
            settings: BOARD_DND_SETTINGS,
            changeColumnOrder: () => {},
            changeRowOrder,
        });
    }, [changeRowOrder, rowsMap, mounted]);
    return (
        <>
            <output id="receipts">{JSON.stringify(receipts)}</output>
            <button onClick={() => setMounted(false)}>Unmount board</button>
            {mounted && (
                <div id="mobile-board" ref={scrollableRef} style={{ display: "flex", overflowX: "auto", width: "100%", height: 600, gap: 12 }}>
                    {columns.map((column) => (
                        <BoardColumn key={column.uid} column={column} isDefaultCardColumn={column.uid === "source"} updateBoard={() => {}} />
                    ))}
                </div>
            )}
        </>
    );
}
const router = createBrowserRouter([
    {
        path: "*",
        element: (
            <BoardProvider project={project} currentUser={user}>
                <NativeBoard />
            </BoardProvider>
        ),
    },
]);
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
