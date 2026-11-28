import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser } from "@/core/models";

export interface IUseUserNotificationMutatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    currentUser: AuthUser.TModel;
}

const useUserNotificationMutatedHandlers = ({ callback, currentUser }: IUseUserNotificationMutatedHandlersProps) => {
    return useSocketHandler({
        topic: ESocketTopic.UserPrivate,
        topicId: currentUser.uid,
        eventKey: `user-notification-mutated-${currentUser.uid}`,
        onProps: {
            name: SocketEvents.SERVER.USER.NOTIFICATION_MUTATED,
            callback: () => callback?.({}),
        },
    });
};

export default useUserNotificationMutatedHandlers;
