import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { WebhookModel } from "@/core/models";
import { ESocketTopic, ESettingSocketTopicID } from "@langboard/core/enums";

export interface IWebhookUpdatedRawResponse {
    name?: string;
    url?: string;
    is_enabled?: bool;
    events?: string[] | null;
    created_at?: Date;
    last_used_at?: Date;
    total_used_count?: number;
}

export interface IUseWebhookUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    webhook: WebhookModel.TModel;
}

const useWebhookUpdatedHandlers = ({ callback, webhook }: IUseWebhookUpdatedHandlersProps) => {
    return useSocketHandler<{}, IWebhookUpdatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.Webhook,
        eventKey: `webhook-updated-${webhook.uid}`,
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.WEBHOOKS.UPDATED,
            params: { uid: webhook.uid },
            callback,
            responseConverter: (data) => {
                Object.entries(data).forEach(([key, value]) => {
                    webhook[key] = value as never;
                });

                return {};
            },
        },
    });
};

export default useWebhookUpdatedHandlers;
