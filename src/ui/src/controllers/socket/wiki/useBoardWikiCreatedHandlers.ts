import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectWiki } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiCreatedRawResponse {
    wiki: Omit<ProjectWiki.IStore, "content" | "editor_generation">;
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
                // Register creation metadata without replaying a queued editor snapshot.
                const { uid, project_uid, title, order, is_public, forbidden, assigned_members, created_at, updated_at } = data.wiki;
                if (!ProjectWiki.Model.getModel(uid)) {
                    ProjectWiki.Model.fromOne(
                        { uid, project_uid, title, order, is_public, forbidden, assigned_members, created_at, updated_at },
                        true
                    );
                }
                const query = { queryKey: [`get-wikis-${projectUID}`], exact: true };
                void queryClient.cancelQueries(query);
                void queryClient.invalidateQueries(query);
                return {};
            },
        },
    });
};

export default useBoardWikiCreatedHandlers;
