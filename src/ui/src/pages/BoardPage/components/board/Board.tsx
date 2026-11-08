"use client";

import { useBoardViewport } from "@/core/providers/BoardViewportProvider";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import invariant from "tiny-invariant";
import BoardColumn, { SkeletonBoardColumn } from "@/pages/BoardPage/components/board/BoardColumn";
import { useBoard } from "@/core/providers/BoardProvider";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import ScrollArea from "@/components/base/ScrollArea";
import BoardColumnAdd from "@/pages/BoardPage/components/board/BoardColumnAdd";
import useChangeProjectColumnOrder from "@/controllers/api/board/useChangeProjectColumnOrder";
import CardFamilyMoveConfirmation from "./CardFamilyMoveConfirmation";
import useChangeCardOrder from "@/controllers/api/board/useChangeCardOrder";
import { BOARD_DND_SETTINGS, BOARD_DND_SYMBOL_SET } from "@/pages/BoardPage/components/board/BoardConstants";
import { SkeletonUserAvatarList } from "@/components/UserAvatarList";
import { SkeletonBoardFilter } from "@/pages/BoardPage/components/board/BoardFilter";
import { useTranslation } from "react-i18next";
import { columnRowDndHelpers } from "@/core/helpers/dnd";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useColumnReordered from "@/core/hooks/useColumnReordered";
import { useBoardController } from "@/core/providers/BoardController";
import useBoardTouchCardDnd from "@/pages/BoardPage/components/board/useBoardTouchCardDnd";
import BoardCardRelationshipOverlay from "@/pages/BoardPage/components/board/BoardCardRelationshipOverlay";
import BoardMinimap from "@/pages/BoardPage/components/board/BoardMinimap";
import useBoardPanning from "./useBoardPanning";
import useBoardSplit from "@/core/hooks/useBoardSplit";
import BoardSplitChooser from "@/pages/BoardPage/BoardSplitChooser";
import { usePageSearchParams } from "@/core/hooks/usePageSearchParams";
import { ProjectRole } from "@/core/models/roles";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";

export function SkeletonBoard() {
    const [t] = useTranslation();
    const cardCounts = [2, 3, 2];

    return (
        <Flex direction="col" h="full" minH="0" data-board-loading="">
            <span role="status" className="sr-only">
                {t("common.Loading...")}
            </span>
            <Box aria-hidden="true" className="flex min-h-0 flex-1 flex-col">
                <Flex justify="between" px="4" pt="4" wrap className="shrink-0">
                    <SkeletonUserAvatarList count={6} size={{ initial: "sm", xs: "default" }} spacing="none" />
                    <Flex items="center" gap="1">
                        <SkeletonBoardFilter />
                    </Flex>
                </Flex>

                <Box position="relative" className="min-h-0 flex-1 overflow-hidden">
                    <Box size="full" className="rounded-[inherit]">
                        <Flex direction="row" items="start" gap="4" p="4">
                            {cardCounts.map((count, index) => (
                                <SkeletonBoardColumn key={index} cardCount={count} />
                            ))}
                        </Flex>
                    </Box>
                </Box>
            </Box>
        </Flex>
    );
}

export function Board() {
    const [t] = useTranslation();
    const { project } = useBoard();
    const split = useBoardSplit();
    const [splitChooserOpen, setSplitChooserOpen] = useState(false);
    const chooseSplit = useCallback(() => setSplitChooserOpen(true), []);
    const { scrollportId, registerViewport } = useBoardViewport();
    const scrollableRef = useRef<HTMLDivElement | null>(null);
    const [scrollable, setScrollable] = useState<HTMLDivElement | null>(null);
    const setScrollableRef = useCallback(
        (node: HTMLDivElement | null) => {
            scrollableRef.current = node;
            setScrollable(node);
            registerViewport(node);
        },
        [registerViewport]
    );

    return (
        <div className="relative flex min-h-0 flex-1 flex-col">
            <ScrollArea.Root
                viewportId={scrollportId}
                className="min-h-0 flex-1"
                viewportClassName="!overflow-x-auto !overflow-y-hidden"
                viewportRef={setScrollableRef}
            >
                <Flex direction="row" items="start" gap="4" p="4" h="full" className="min-h-0">
                    <BoardDisplay scrollable={scrollable} scrollableRef={scrollableRef} chooseSplit={chooseSplit} />
                </Flex>
                <ScrollArea.Bar orientation="horizontal" />
            </ScrollArea.Root>
            <BoardMinimap scrollable={scrollable} scrollportId={scrollportId} />
            {split && split.panes.length < 2 && (
                <Button
                    type="button"
                    variant="outline"
                    size="icon-sm"
                    className="absolute right-2 top-1/2 z-20 hidden -translate-y-1/2 rounded-full shadow-md md:inline-flex"
                    aria-label={t("common.Split view")}
                    title={t("common.Split view")}
                    data-block-board-panning="true"
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={chooseSplit}
                >
                    <IconComponent icon="columns-2" size="4" />
                </Button>
            )}
            {splitChooserOpen && split && split.panes.length < 2 && (
                <BoardSplitChooser currentProjectUID={project.uid} onClose={() => setSplitChooserOpen(false)} />
            )}
        </div>
    );
}

function BoardDisplay({
    scrollable,
    scrollableRef,
    chooseSplit,
}: {
    scrollable: HTMLDivElement | null;
    scrollableRef: React.RefObject<HTMLDivElement | null>;
    chooseSplit: () => void;
}) {
    const { chatResizableSidebar } = useBoardController();
    const { project, columns: flatColumns, cardsMap, socket, canDragAndDrop, canDragCards, hasRoleAction } = useBoard();
    const [, setSearchParams] = usePageSearchParams();
    const canCreate = hasRoleAction(ProjectRole.EAction.CardWrite);
    const compose = useCallback(() => {
        setSearchParams(
            (params) => {
                params.set("new-card", "1");
                return params;
            },
            { replace: true }
        );
    }, [setSearchParams]);
    const split = useBoardSplit();
    useBoardPanning(scrollableRef, canCreate ? compose : undefined, split && split.panes.length < 2 ? chooseSplit : undefined);
    const updater = useReducer((x) => x + 1, 0);
    const [_, forceUpdate] = updater;
    const { columns } = useColumnReordered({
        type: "ProjectColumn",
        topicId: project.uid,
        eventNameParams: { uid: project.uid },
        columns: flatColumns,
        socket,
        updater,
    });
    const { mutate: changeColumnOrderMutate } = useChangeProjectColumnOrder();
    const { mutate: changeCardOrderMutate, confirmation } = useChangeCardOrder();

    const setupApiErrors = useCallback((error: unknown, undo: () => void) => {
        const { handle } = setupApiErrorHandler({
            code: {
                after: undo,
            },
            wildcard: {
                after: undo,
            },
        });

        handle(error);
    }, []);

    const changeColumnOrder = useCallback(
        ({ columnUID, order, undo }: { columnUID: string; order: number; undo: () => void }) => {
            changeColumnOrderMutate(
                { project_uid: project.uid, project_column_uid: columnUID, order },
                {
                    onError: (error) => setupApiErrors(error, undo),
                    onSettled: forceUpdate,
                }
            );
        },
        [changeColumnOrderMutate, forceUpdate, project, setupApiErrors]
    );

    const changeRowOrder = useCallback(
        ({ rowUID, order, parentUID, undo }: { rowUID: string; order: number; parentUID?: string; undo: () => void }) => {
            changeCardOrderMutate(
                { project_uid: project.uid, parent_uid: parentUID, card_uid: rowUID, order },
                {
                    onError: (error) => setupApiErrors(error, undo),
                    onSettled: forceUpdate,
                }
            );
        },
        [changeCardOrderMutate, forceUpdate, project, setupApiErrors]
    );

    useBoardTouchCardDnd({
        enabled: canDragCards,
        scrollableRef,
        columns,
        rowsMap: cardsMap,
        changeRowOrder,
    });

    useEffect(() => {
        const scrollable = scrollableRef.current;
        invariant(scrollable);

        return columnRowDndHelpers.root({
            columns,
            rowsMap: cardsMap,
            columnKeyInRow: "project_column_uid",
            symbolSet: BOARD_DND_SYMBOL_SET,
            scrollable,
            settings: BOARD_DND_SETTINGS,
            changeColumnOrder,
            changeRowOrder,
        });
    }, [cardsMap, changeColumnOrder, changeRowOrder, chatResizableSidebar, columns, scrollableRef]);

    return (
        <>
            <CardFamilyMoveConfirmation {...confirmation} />
            <BoardCardRelationshipOverlay scrollable={scrollable} />
            {columns.map((column) => (
                <BoardColumn
                    key={`board-columnr-${column.uid}`}
                    column={column}
                    updateBoard={forceUpdate}
                    isDefaultCardColumn={column.uid === columns.find((item) => !item.is_archive)?.uid}
                />
            ))}
            {canDragAndDrop && <BoardColumnAdd />}
        </>
    );
}
