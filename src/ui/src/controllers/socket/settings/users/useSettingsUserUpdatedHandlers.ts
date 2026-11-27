import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { User } from "@/core/models";

export interface ISettingsUserUpdatedRawResponse {
    firstname?: string;
    lastname?: string;
    avatar?: string;
    is_admin?: bool;
    industry?: string;
    purpose?: string;
    affiliation?: string;
    position?: string;
    activated_at?: string | Date | null;
}

export interface IUseSettingsUserUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    user: User.TModel;
}

const useSettingsUserUpdatedHandlers = ({ callback, user }: IUseSettingsUserUpdatedHandlersProps) => {
    return useSocketHandler<{}, ISettingsUserUpdatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.User,
        eventKey: `settings-user-updated-${user.uid}`,
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.USERS.UPDATED,
            params: { uid: user.uid },
            callback,
            responseConverter: (data) => {
                Object.entries(data).forEach(([key, value]) => {
                    if (key === "activated_at") {
                        user.activated_at = value ? new Date(value as string | Date) : undefined;
                        return;
                    }

                    user[key] = value as never;
                });

                return {};
            },
        },
    });
};

export default useSettingsUserUpdatedHandlers;
