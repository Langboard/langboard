import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { Project, User } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";
import { ProjectRole } from "@/core/models/roles";

export interface IBoardAssignedUsersUpdatedRawResponse {
    assigned_members: User.Interface[];
    invited_members: User.Interface[];
    invitation_uid?: string;
}

export interface IBoardAssignedUsersUpdatedResponse {
    assigned_user_uids: string[];
}

export interface IUseBoardAssignedUsersUpdatedHandlersProps extends IBaseUseSocketHandlersProps<IBoardAssignedUsersUpdatedResponse> {
    projectUID: string;
}

const useBoardAssignedUsersUpdatedHandlers = ({ callback, projectUID }: IUseBoardAssignedUsersUpdatedHandlersProps) => {
    return useSocketHandler<IBoardAssignedUsersUpdatedResponse, IBoardAssignedUsersUpdatedRawResponse>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-assigned-users-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.ASSIGNED_USERS_UPDATED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                const model = Project.Model.getModel(projectUID);
                if (model) {
                    model.all_members = [
                        model.all_members.find((user) => user.uid === model.owner_uid)!,
                        ...data.assigned_members,
                        ...data.invited_members,
                    ];
                    model.invited_member_uids = data.invited_members.map((user) => user.uid);

                    const memberRoles = { ...(model.member_roles ?? {}) };
                    Object.keys(memberRoles).forEach((userUID) => {
                        if (!model.all_members.some((member) => member.uid === userUID)) {
                            delete memberRoles[userUID];
                        }
                    });
                    for (let i = 0; i < data.assigned_members.length; ++i) {
                        const assignedMember = data.assigned_members[i];
                        if (!memberRoles[assignedMember.uid]) {
                            memberRoles[assignedMember.uid] = [ProjectRole.EAction.Read];
                        }
                    }
                    model.member_roles = memberRoles;
                }

                if (data.invitation_uid) {
                    User.Model.deleteModel(data.invitation_uid);
                }

                return {
                    assigned_user_uids: data.assigned_members.map((user) => user.uid),
                };
            },
        },
    });
};

export default useBoardAssignedUsersUpdatedHandlers;
