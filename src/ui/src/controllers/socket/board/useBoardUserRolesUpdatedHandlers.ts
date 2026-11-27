import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { Project } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";
import { ProjectRole } from "@/core/models/roles";

export interface IBoardUserRolesUpdatedRawResponse {
    user_uid: string;
    roles: ProjectRole.TActions[];
}

export interface IUseBoardUserRolesUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardUserRolesUpdatedHandlers = ({ callback, projectUID }: IUseBoardUserRolesUpdatedHandlersProps) => {
    return useSocketHandler<{}, IBoardUserRolesUpdatedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-user-roles-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.USER_ROLES_UPDATED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                const model = Project.Model.getModel(projectUID);
                if (model) {
                    const memberRoles = { ...(model.member_roles ?? {}) };
                    memberRoles[data.user_uid] = data.roles;
                    model.member_roles = memberRoles;
                }

                return {};
            },
        },
    });
};

export default useBoardUserRolesUpdatedHandlers;
