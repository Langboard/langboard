import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectWiki } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiCreatedRawResponse {
    wiki: ProjectWiki.IStore;
}

export interface IUseBoardWikiCreatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useBoardWikiCreatedHandlers = ({ callback, projectUID }: IUseBoardWikiCreatedHandlersProps) => {
    return useSocketHandler<{}, IBoardWikiCreatedRawResponse>({
        topic: ESocketTopic.BoardWiki,
        topicId: projectUID,
        eventKey: `board-wiki-created-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.WIKI.CREATED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                resetWorkspaceSummaryQueries(queryClient, "wiki-summaries", projectUID, data);
                ProjectWiki.Model.fromOne(data.wiki, true);
                return {};
            },
        },
    });
};

export default useBoardWikiCreatedHandlers;
