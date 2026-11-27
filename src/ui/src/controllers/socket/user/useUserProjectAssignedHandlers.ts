import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IUserProjectAssignedRawResponse {
    project_uid: string;
}

export interface IUseUserProjectAssignedHandlersProps extends IBaseUseSocketHandlersProps<IUserProjectAssignedRawResponse> {
    currentUser: AuthUser.TModel;
}

const useUserProjectAssignedHandlers = ({ callback, currentUser }: IUseUserProjectAssignedHandlersProps) => {
    return useSocketHandler<IUserProjectAssignedRawResponse>({
        topic: ESocketTopic.UserPrivate,
        topicId: currentUser.uid,
        eventKey: `user-project-assigned-${currentUser.uid}`,
        onProps: {
            name: SocketEvents.SERVER.USER.PROJECT_ASSIGNED,
            callback,
        },
    });
};

export default useUserProjectAssignedHandlers;
