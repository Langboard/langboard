import { useRef } from "react";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";
import { Project, ProjectBotScope, ProjectBotSchedule, ProjectColumn, ProjectColumnBotScope, ProjectColumnBotSchedule } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";

interface IBoardAutomationResponse {
    project_bot_scopes: ProjectBotScope.Interface[];
    project_bot_schedules: ProjectBotSchedule.Interface[];
    column_bot_scopes: ProjectColumnBotScope.Interface[];
    column_bot_schedules: ProjectColumnBotSchedule.Interface[];
}

const useGetBoardAutomation = (projectUID: string) => {
    const { query } = useQueryMutation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const currentBoard = useRef(projectUID);
    currentBoard.current = projectUID;
    const project = Project.Model.useModel(projectUID, [projectUID]);
    const actions = project?.current_auth_role_actions;
    const canRead = !!actions?.some((action) => action === "read" || action === ROLE_ALL_GRANTED);

    return query(
        ["get-board-automation", actorUID, session, projectUID],
        async ({ signal }) => {
            const board = Project.Model.getModel(projectUID);
            const url = `${Utils.String.format(Routing.API.BOARD.GET, { uid: projectUID })}/automation`;
            const response = await api.get<IBoardAutomationResponse>(url, { signal, env: { interceptToast: false } as never });
            signal.throwIfAborted();
            const auth = useAuthStore.getState();
            const current = Project.Model.getModel(projectUID);
            if (
                !actorUID ||
                actorUID !== auth.currentUser?.uid ||
                session !== auth.getSessionVersion() ||
                currentBoard.current !== projectUID ||
                !current ||
                current !== board ||
                !current.current_auth_role_actions?.some((action) => action === "read" || action === ROLE_ALL_GRANTED)
            ) {
                throw new Error("Board automation request no longer authorized");
            }
            const data = response.data;
            if (
                !data ||
                ![data.project_bot_scopes, data.project_bot_schedules, data.column_bot_scopes, data.column_bot_schedules].every(Array.isArray)
            ) {
                throw new Error("Incomplete board automation response");
            }
            const columnUIDs = new Set(ProjectColumn.Model.getModels((column) => column.project_uid === projectUID).map((column) => column.uid));
            if (
                data.project_bot_scopes.some((scope) => scope.project_uid !== projectUID) ||
                data.project_bot_schedules.some((schedule) => schedule.project_uid !== projectUID) ||
                data.column_bot_scopes.some((scope) => !columnUIDs.has(scope.project_column_uid)) ||
                data.column_bot_schedules.some((schedule) => !columnUIDs.has(schedule.project_column_uid))
            ) {
                throw new Error("Board automation response belongs to another board");
            }
            ProjectBotScope.Model.fromArray(data.project_bot_scopes, true);
            ProjectBotSchedule.Model.fromArray(data.project_bot_schedules, true);
            ProjectColumnBotScope.Model.fromArray(data.column_bot_scopes, true);
            ProjectColumnBotSchedule.Model.fromArray(data.column_bot_schedules, true);
            return true;
        },
        {
            enabled: !!actorUID && canRead,
            staleTime: Infinity,
            retry: false,
            retryOnMount: false,
            refetchOnMount: false,
            refetchOnWindowFocus: false,
            refetchOnReconnect: false,
            refetchInterval: false,
        }
    );
};

export default useGetBoardAutomation;
