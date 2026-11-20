import { memo } from "react";
import { StaticMarkdownContent } from "@/components/Editor/static-markdown-content";

import Box from "@/components/base/Box";
import { EditorDataProvider } from "@/core/providers/EditorDataProvider";
import { AuthUser, ProjectCard } from "@/core/models";
import type { TUserLikeModel } from "@/core/models/ModelRegistry";

import type { IDescriptionChunk } from "./descriptionChunks";

interface IBoardCardDescriptionStaticChunkProps {
    chunk: IDescriptionChunk;
    currentUser: AuthUser.TModel;
    mentionables: TUserLikeModel[];
    cards: ProjectCard.TModel[];
    projectUID: string;
    cardUID: string;
}

export const BoardCardDescriptionStaticChunk = memo(
    ({ chunk, currentUser, mentionables, cards, projectUID, cardUID }: IBoardCardDescriptionStaticChunkProps): React.JSX.Element => {
        return (
            <Box
                data-card-description-chunk
                data-description-chunk-id={chunk.id}
                data-description-card-uid={cardUID}
                className="[contain-intrinsic-size:auto_160px]"
            >
                <EditorDataProvider
                    currentUser={currentUser}
                    mentionables={mentionables}
                    linkables={cards}
                    editorType="view"
                    form={{ project_uid: projectUID, card_uid: cardUID }}
                >
                    <StaticMarkdownContent content={chunk.content} />
                </EditorDataProvider>
            </Box>
        );
    }
);

export default BoardCardDescriptionStaticChunk;
