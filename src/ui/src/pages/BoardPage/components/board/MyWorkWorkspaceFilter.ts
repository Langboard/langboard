import type { MyWorkFilter } from "../../../DashboardPage/my-work-context/MyWorkContext";

export interface MyWorkWorkspaceSelection {
    actorUID: string;
    identityVersion: number;
    projectUID: string;
    filter: MyWorkFilter;
}

/** Each board pane owns its selection; a new identity or board cannot inherit it. */
export function currentMyWorkWorkspaceFilter(
    selection: MyWorkWorkspaceSelection | null,
    actorUID: string | undefined,
    identityVersion: number,
    projectUID: string
): MyWorkFilter | undefined {
    return selection && selection.actorUID === actorUID && selection.identityVersion === identityVersion && selection.projectUID === projectUID
        ? selection.filter
        : undefined;
}
