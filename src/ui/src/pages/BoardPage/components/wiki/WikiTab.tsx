import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import Skeleton from "@/components/base/Skeleton";
import Tabs from "@/components/base/Tabs";
import Toast from "@/components/base/Toast";
import Tooltip from "@/components/base/Tooltip";
import useDeleteWiki from "@/controllers/api/wiki/useDeleteWiki";
import { renderClonedPreview } from "@/core/helpers/dnd/renderClonedPreview";
import { singleDndHelpers } from "@/core/helpers/dnd";
import { SINGLE_ROW_IDLE } from "@/core/helpers/dnd/createDndSingleRowEvents";
import { TSingleRowState } from "@/core/helpers/dnd/types";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ProjectWiki } from "@/core/models";
import { useBoardWiki } from "@/core/providers/BoardWikiProvider";
import { cn } from "@/core/utils/ComponentUtils";
import { BOARD_WIKI_DND_SYMBOL_SET } from "@/pages/BoardPage/components/wiki/WikiConstants";
import { DropIndicator } from "@atlaskit/pragmatic-drag-and-drop-react-drop-indicator/box";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import invariant from "tiny-invariant";

export interface IWikiTabProps {
    wiki: ProjectWiki.TModel;
}

export function SkeletonWikiTab() {
    return <Skeleton h="8" w={{ initial: "14", sm: "20", md: "28" }} />;
}

function WikiTab({ wiki }: IWikiTabProps) {
    const { modeType } = useBoardWiki();
    const [state, setState] = useState<TSingleRowState>(SINGLE_ROW_IDLE);
    const order = wiki.useField("order");
    const forbidden = wiki.useField("forbidden");
    const outerRef = useRef<HTMLDivElement | null>(null);
    const draggableRef = useRef<HTMLButtonElement | null>(null);

    useEffect(() => {
        if (forbidden || modeType !== "reorder") {
            return;
        }

        const outer = outerRef.current;
        const draggable = draggableRef.current;
        invariant(outer && draggable);

        return singleDndHelpers.row({
            row: wiki,
            symbolSet: BOARD_WIKI_DND_SYMBOL_SET,
            draggable: draggable,
            dropTarget: outer,
            isHorizontal: true,
            setState,
            renderPreview({ container }) {
                renderClonedPreview(outer, container);
            },
        });
    }, [wiki, order, forbidden, modeType]);

    return (
        <Box position="relative" ref={outerRef}>
            {state.type === "is-over" && <DropIndicator edge={state.closestEdge} gap="4px" />}
            <WikiTabDisplay wiki={wiki} draggableRef={draggableRef} />
        </Box>
    );
}

interface IWikiTabDisplayProps {
    wiki: ProjectWiki.TModel;
    draggableRef?: React.RefObject<HTMLButtonElement | null>;
}

const WikiTabDisplay = memo(({ wiki, draggableRef }: IWikiTabDisplayProps) => {
    const [t] = useTranslation();
    const { project, modeType, changeTab } = useBoardWiki();
    const forbidden = wiki.useField("forbidden");
    const title = wiki.useField("title");
    const { mutateAsync: deleteWikiMutateAsync } = useDeleteWiki({ interceptToast: true });

    const handleTriggerClick = useCallback(() => {
        if (forbidden) {
            return;
        }

        changeTab(wiki.uid);
    }, [changeTab, forbidden]);

    const deleteWiki = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();

        const promise = deleteWikiMutateAsync({
            project_uid: project.uid,
            wiki_uid: wiki.uid,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Deleting..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Wiki page deleted successfully.");
            },
            finally: () => {},
        });
    };

    return (
        <Tooltip.Root>
            <Tabs.Trigger
                value={wiki.uid}
                id={`board-wiki-${wiki.uid}-tab`}
                disabled={forbidden}
                className={cn("cursor-pointer ring-primary", modeType === "delete" && "pr-1")}
                onClick={handleTriggerClick}
                ref={draggableRef}
            >
                <Tooltip.Trigger asChild>
                    <span className="max-w-40 truncate">{forbidden ? t("wiki.Private") : title}</span>
                </Tooltip.Trigger>
                {modeType === "delete" && (
                    <Button
                        asChild
                        variant="destructive-ghost"
                        size="icon-sm"
                        title={t("common.Delete")}
                        className="ml-2 size-6"
                        onClick={deleteWiki}
                    >
                        <span>
                            <IconComponent icon="trash-2" size="3" />
                        </span>
                    </Button>
                )}
            </Tabs.Trigger>
            <Tooltip.Content>{forbidden ? t("wiki.Private") : title}</Tooltip.Content>
        </Tooltip.Root>
    );
});

export default WikiTab;
