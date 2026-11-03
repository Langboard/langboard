export const WIKI_CARD_DRAG_TYPE = "board-wiki-linked-card";
export const WIKI_CARD_DRAG_SOURCE_ATTR = "data-wiki-card-drag-source";

export function isWikiCardDragData(
    data: Record<string | symbol, unknown>,
    projectUID: string
): data is {
    type: typeof WIKI_CARD_DRAG_TYPE;
    projectUID: string;
    wikiUID: string;
} {
    return data.type === WIKI_CARD_DRAG_TYPE && data.projectUID === projectUID && typeof data.wikiUID === "string" && !!data.wikiUID;
}
