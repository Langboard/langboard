import { SocketEvents } from "@langboard/core/constants";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ApiComfortToolModel } from "@/core/models";

export interface IApiComfortToolDeletedRawResponse {
    uid: string;
    name: string;
}

export interface IUseApiComfortToolDeletedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    apiComfortTool?: ApiComfortToolModel.TModel;
}

const useApiComfortToolDeletedHandlers = ({ apiComfortTool, callback }: IUseApiComfortToolDeletedHandlersProps) => {
    const eventKey = apiComfortTool ? `api-comfort-tool-deleted-${apiComfortTool.uid}` : "api-comfort-tool-deleted";
    const name = apiComfortTool ? SocketEvents.SERVER.SETTINGS.API_COMFORT_TOOL.DELETED : SocketEvents.SERVER.SETTINGS.API_COMFORT_TOOL.DELETED_ANY;
    const params = apiComfortTool ? { uid: apiComfortTool.uid } : undefined;

    return useSocketHandler<{}, IApiComfortToolDeletedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.ApiComfortTool,
        eventKey,
        onProps: {
            name,
            params,
            callback,
            responseConverter: (data) => {
                ApiComfortToolModel.Model.deleteModel(data.uid);
                return {};
            },
        },
    });
};

export default useApiComfortToolDeletedHandlers;
