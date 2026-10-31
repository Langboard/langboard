import { useEffect, useRef, useState, type ReactNode } from "react";
import { dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { useTranslation } from "react-i18next";
import { ProjectColumn } from "@/core/models";
import { useBoard } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import useCreateWikiLinkedCard from "@/controllers/api/wiki/useCreateWikiLinkedCard";
import Toast from "@/components/base/Toast";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { BOARD_CARD_FOCUS_EVENT, type IBoardCardFocusEventDetail } from "./BoardConstants";
import { isWikiCardDragData, WIKI_CARD_DRAG_SOURCE_ATTR } from "./WikiCardDropData";

// A source can be dropped over different columns while its first request is pending.
const pendingWikiLinks = new Set<string>();

export default function WikiCardDrop({ column, children }: { column: ProjectColumn.TModel; children: ReactNode }) {
    const [t] = useTranslation();
    const { canDragCards } = useBoard();
    const isArchive = column.useField("is_archive");
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const target = useRef<HTMLDivElement>(null);
    const [over, setOver] = useState(false);
    const { mutateAsync: createLinkedCard, isPending } = useCreateWikiLinkedCard({ interceptToast: true });

    useEffect(() => {
        if (!target.current || !canDragCards || isArchive || !actor) return;
        let active = true;
        const key = (wikiUID: string) => JSON.stringify([actor, session, column.project_uid, wikiUID]);
        const release = dropTargetForElements({
            element: target.current,
            canDrop: ({ source }) =>
                isWikiCardDragData(source.data, column.project_uid) &&
                source.element.isConnected &&
                source.element.getAttribute(WIKI_CARD_DRAG_SOURCE_ATTR) === source.data.wikiUID &&
                !pendingWikiLinks.has(key(source.data.wikiUID)),
            getData: () => ({ wikiCardColumnUID: column.uid }),
            onDragEnter: () => setOver(true),
            onDragLeave: () => setOver(false),
            onDrop: ({ source }) => {
                setOver(false);
                if (
                    !isWikiCardDragData(source.data, column.project_uid) ||
                    !source.element.isConnected ||
                    source.element.getAttribute(WIKI_CARD_DRAG_SOURCE_ATTR) !== source.data.wikiUID
                )
                    return;
                const requestKey = key(source.data.wikiUID);
                if (pendingWikiLinks.has(requestKey)) return;
                pendingWikiLinks.add(requestKey);
                const promise = createLinkedCard({ project_uid: column.project_uid, wiki_uid: source.data.wikiUID, column_uid: column.uid });
                Toast.Add.promise(promise, {
                    loading: t("common.Creating..."),
                    error: createApiErrorMessageHandler(),
                    success: ({ created }) => (created ? t("wiki.Linked card created") : t("wiki.Linked card already exists")),
                });
                void promise
                    .then(({ card }) => {
                        // Use the server's column, including an existing link in another column.
                        requestAnimationFrame(() =>
                            requestAnimationFrame(() => {
                                if (!active) return;
                                document.dispatchEvent(
                                    new CustomEvent<IBoardCardFocusEventDetail>(BOARD_CARD_FOCUS_EVENT, {
                                        detail: { cardUID: card.uid, columnUID: card.project_column_uid, focus: true },
                                    })
                                );
                            })
                        );
                    })
                    .catch(() => {
                        // The localized toast presents the failure; a new drop deliberately retries.
                    })
                    .finally(() => pendingWikiLinks.delete(requestKey));
            },
        });
        return () => {
            active = false;
            release();
        };
    }, [actor, session, column, canDragCards, isArchive, createLinkedCard, t]);

    return (
        <div
            ref={target}
            data-wiki-card-column={column.uid}
            aria-busy={isPending}
            className={over ? "flex shrink-0 rounded-2xl ring-2 ring-primary" : "flex shrink-0"}
        >
            {children}
        </div>
    );
}
