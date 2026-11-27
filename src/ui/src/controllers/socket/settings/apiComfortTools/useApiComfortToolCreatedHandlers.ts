import { SocketEvents } from "@langboard/core/constants";
import { ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ApiComfortToolModel } from "@/core/models";

export interface IApiComfortToolCreatedRawResponse {
    api_comfort_tool: ApiComfortToolModel.Interface;
}

const useApiComfortToolCreatedHandlers = ({ callback }: IBaseUseSocketHandlersProps<{}>) => {
    return useSocketHandler<{}, IApiComfortToolCreatedRawResponse>({
        topic: ESocketTopic.AppSettings,
        topicId: ESettingSocketTopicID.ApiComfortTool,
        eventKey: "api-comfort-tool-created",
        onProps: {
            name: SocketEvents.SERVER.SETTINGS.API_COMFORT_TOOL.CREATED,
            callback,
            responseConverter: (data) => {
                ApiComfortToolModel.Model.fromOne(data.api_comfort_tool, true);
                return {};
            },
        },
    });
};

export default useApiComfortToolCreatedHandlers;
