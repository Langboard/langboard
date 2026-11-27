import { SocketEvents } from "@langboard/core/constants";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ApiComfortToolModel } from "@/core/models";

export interface IApiComfortToolUpdatedRawResponse {
    api_comfort_tool: ApiComfortToolModel.Interface;
}

export interface IUseApiComfortToolUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    apiComfortTool?: ApiComfortToolModel.TModel;
}

const useApiComfortToolUpdatedHandlers = ({ apiComfortTool, callback }: IUseApiComfortToolUpdatedHandlersProps) => {
    const eventKey = apiComfortTool ? `api-comfort-tool-updated-${apiComfortTool.uid}` : "api-comfort-tool-updated";
    const name = apiComfortTool ? SocketEvents.SERVER.SETTINGS.API_COMFORT_TOOL.UPDATED : SocketEvents.SERVER.SETTINGS.API_COMFORT_TOOL.UPDATED_ANY;
    const params = apiComfortTool ? { uid: apiComfortTool.uid } : undefined;

    return useSocketHandler<{}, IApiComfortToolUpdatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.ApiComfortTool,
        eventKey,
        onProps: {
            name,
            params,
            callback,
            responseConverter: (data) => {
                ApiComfortToolModel.Model.fromOne(data.api_comfort_tool, true);
                return {};
            },
        },
    });
};

export default useApiComfortToolUpdatedHandlers;
