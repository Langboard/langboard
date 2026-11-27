import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { User } from "@/core/models";
import { ESocketTopic, ESettingSocketTopicID } from "@langboard/core/enums";

export interface IUserCreatedRawResponse {
    user: User.Interface;
}

const useUserCreatedHandlers = ({ callback }: IBaseUseSocketHandlersProps<{}>) => {
    return useSocketHandler<{}, IUserCreatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.User,
        eventKey: "user-created",
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.USERS.CREATED,
            callback,
            responseConverter: (data) => {
                User.Model.fromOne(data.user, true);
                return {};
            },
        },
    });
};

export default useUserCreatedHandlers;
