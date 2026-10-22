import { useTranslation } from "react-i18next";
import { ProjectColumn, type ProjectCard, type Project } from "@/core/models";
import { EAction } from "@/core/models/roles/ProjectRole";
import useAuthStore from "@/core/stores/AuthStore";
import useSocketConnectionStatus from "@/core/hooks/useSocketConnectionStatus";

export interface IWorkbenchStatusContext {
    userUID: string;
    sessionVersion: number;
    card?: ProjectCard.TModel;
    project: Project.TModel;
    view: string;
    unreadCount?: number;
}

export default function WorkbenchStatusBar({ context }: { context?: IWorkbenchStatusContext }) {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const connection = useSocketConnectionStatus();
    const board = actor && context?.userUID === actor && context.sessionVersion === session ? context : undefined;
    const status = actor && connection.userUID === actor ? connection.status : "disconnected";
    const connectionLabels = {
        disconnected: t("dashboard.status.Disconnected"),
        connecting: t("dashboard.status.Connecting"),
        open: t("dashboard.status.Socket open"),
        error: t("dashboard.status.Connection error"),
        closed: t("dashboard.status.Connection closed"),
    };
    return (
        <footer
            aria-label={t("dashboard.status.Workbench status")}
            data-workbench-status=""
            className={[
                "fixed inset-x-0 bottom-[var(--workbench-navigation-inset,0px)] z-40 h-[var(--workbench-status-height)]",
                "border-t bg-background pb-[var(--workbench-status-safe,0px)] text-xs text-muted-foreground",
            ].join(" ")}
        >
            <div className="flex h-8 items-center gap-3 whitespace-nowrap px-3">
                <div className="flex min-w-0 flex-1 items-center gap-4 overflow-x-auto">
                    {board && <BoardStatusContext key={`${board.userUID}:${board.project.uid}`} context={board} />}
                    <span>
                        {t("dashboard.status.MCP")}: {t("dashboard.status.Unverified")}
                    </span>
                </div>
                <span data-status-realtime="" className="shrink-0" title={t("dashboard.status.Socket transport only")}>
                    {t("dashboard.status.Realtime")}: {connectionLabels[status]}
                </span>
            </div>
        </footer>
    );
}

function BoardStatusContext({ context }: { context: IWorkbenchStatusContext }) {
    const [t] = useTranslation();
    const title = context.project.useField("title");
    const members = context.project.useForeignFieldArray("all_members");
    const permissions = context.project.useField("current_auth_role_actions") ?? [];
    const permissionLabels = (permissions.includes("*") ? Object.values(EAction) : permissions.map((action) => action.toLowerCase()))
        .filter((action) => Object.values<string>(EAction).includes(action))
        .map((action) => t(`role.project.${action}`));
    return (
        <>
            <span data-status-project="" className="shrink-0" title={title}>
                {title}
            </span>
            <span>{context.card ? <CardColumnStatus key={context.card.uid} card={context.card} fallback={context.view} /> : context.view}</span>
            <span>
                {t("dashboard.status.Unread")}: {context.unreadCount ?? t("dashboard.status.Unverified")}
            </span>
            <span>
                {t("dashboard.status.Members")}: {members.length}
            </span>
            <span title={permissionLabels.join(", ")}>
                {t("dashboard.status.Permissions")}: {permissionLabels.join(", ") || t("dashboard.status.None")}
            </span>
        </>
    );
}

function CardColumnStatus({ card, fallback }: { card: ProjectCard.TModel; fallback: string }) {
    const columnUID = card.useField("project_column_uid");
    const column = ProjectColumn.Model.useModel(columnUID, [columnUID]);
    return column?.uid === columnUID && column.project_uid === card.project_uid ? <ColumnName key={columnUID} column={column} /> : fallback;
}

function ColumnName({ column }: { column: ProjectColumn.TModel }) {
    return column.useField("name");
}
