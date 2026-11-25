import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

interface INotificationNotice {
    notification?: { uid?: string };
}

export interface IUseUserNotifiedHandlersProps extends IBaseUseSocketHandlersProps<INotificationNotice> {
    currentUser: AuthUser.TModel;
}

const useUserNotifiedHandlers = ({ callback, currentUser }: IUseUserNotifiedHandlersProps) => {
    return useSocketHandler<INotificationNotice>({
        topic: ESocketTopic.UserPrivate,
        topicId: currentUser.uid,
        eventKey: `user-notified-${currentUser.uid}`,
        onProps: {
            name: SocketEvents.SERVER.USER.NOTIFIED,
            callback,
        },
    });
};

export default useUserNotifiedHandlers;
