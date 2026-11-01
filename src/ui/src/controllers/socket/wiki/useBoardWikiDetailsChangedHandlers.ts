import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectWiki } from "@/core/models";
import { IEditorContent } from "@/core/models/Base";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiDetailsChangedRawResponse {
    title?: string;
    content?: IEditorContent;
    editor_generation?: number;
}

export interface IUseBoardWikiDetailsChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    wiki: ProjectWiki.TModel;
    isPrivate: bool;
}

const useBoardWikiDetailsChangedHandlers = ({ callback, projectUID, wiki, isPrivate }: IUseBoardWikiDetailsChangedHandlersProps) => {
    const topic = isPrivate ? ESocketTopic.BoardWikiPrivate : ESocketTopic.BoardWiki;
    const topicId = isPrivate ? wiki.uid : projectUID;

    return useSocketHandler<{}, IBoardWikiDetailsChangedRawResponse>({
        topic,
        topicId,
        eventKey: `board-wiki-details-changed-${topic}-${wiki.uid}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.WIKI.DETAILS_CHANGED,
            params: { uid: wiki.uid },
            callback,
            responseConverter: (data) => {
                resetWorkspaceSummaryQueries(queryClient, "wiki-summaries", projectUID, data);
                const generation = data.editor_generation;
                if (generation !== undefined && generation < (wiki.editor_generation ?? 0)) return {};
                if (generation !== undefined && generation > (wiki.editor_generation ?? 0)) {
                    // Invalidate the old editor immediately, including when the authorized read fails.
                    wiki.content = undefined;
                    wiki.editor_generation = generation;
                }
                if (data.title !== undefined) wiki.title = data.title;
                if (generation !== undefined || data.content !== undefined) {
                    // A body received from an older queued event is never authoritative.
                    // Keep ordinary edits in the same editor lineage while the native query refreshes.
                    const query = { queryKey: [`get-wikis-${projectUID}`], exact: true };
                    void queryClient.cancelQueries(query);
                    void queryClient.invalidateQueries(query);
                }
                return {};
            },
        },
    });
};

export default useBoardWikiDetailsChangedHandlers;
