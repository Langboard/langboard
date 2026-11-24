/* eslint-disable @typescript-eslint/no-explicit-any */
import { applyOptimisticOrder } from "@/core/helpers/dnd/applyOptimisticOrder";
import { extractClosestEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";
import { reorderWithEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/util/reorder-with-edge";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { monitorForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { reorder } from "@atlaskit/pragmatic-drag-and-drop/reorder";
import { Edge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/dist/types/types";
import { TColumnRowSettings, TColumnRowSymbolSet } from "@/core/helpers/dnd/types";
import createDndColumnRowDataHelper from "@/core/helpers/dnd/createDndColumnRowDataHelper";
import { canReorderByClosestEdge } from "@/core/helpers/dnd/utils";
import { TOrderableModel, TOrderableModelName } from "@/core/models/ModelRegistry";
import { unsafeOverflowAutoScrollForElements } from "@/core/helpers/dnd/auto-scroll/entry-point/unsafe-overflow/element";
import { autoScrollForElements } from "@/core/helpers/dnd/auto-scroll/entry-point/element";

export interface ICreateDndRootEventsProps<
    TColumnModel extends TOrderableModel<TOrderableModelName>,
    TRowModel extends TOrderableModel<TOrderableModelName>,
> {
    columns: TColumnModel[];
    rowsMap: Record<string, TRowModel>;
    columnKeyInRow: keyof ICreateDndRootEventsProps<TColumnModel, TRowModel>["rowsMap"][string];
    symbolSet: TColumnRowSymbolSet;
    scrollable: HTMLElement;
    settings: TColumnRowSettings;
    isIndicator?: bool;
    changeColumnOrder: (context: { columnUID: string; order: number; undo: () => void }) => void;
    changeRowOrder: (context: { rowUID: string; order: number; parentUID?: string; undo: () => void }) => void;
}

const createDndRootEvents = <TColumnModel extends TOrderableModel<TOrderableModelName>, TRowModel extends TOrderableModel<TOrderableModelName>>(
    props: ICreateDndRootEventsProps<TColumnModel, TRowModel>
) => {
    const { columns, rowsMap, columnKeyInRow, symbolSet, scrollable, settings, isIndicator, changeColumnOrder, changeRowOrder } = props;

    const { isColumnData, isDraggingAColumn, isColumnDroppableTargetData, isRowData, isDraggingARow, isRowDroppableTargetData } =
        createDndColumnRowDataHelper<TColumnModel, TRowModel>({
            symbolSet,
        });

    const getRowsByColumn = (column: TColumnModel): TRowModel[] => {
        return Object.values(rowsMap)
            .filter((row) => row[columnKeyInRow] === column.uid)
            .sort((a, b) => a.order - b.order);
    };

    const reorderItems = <TValue extends TColumnModel | TRowModel>({
        list,
        startIndex,
        finishIndex,
    }: {
        list: TValue[];
        startIndex: number;
        finishIndex: number | "last";
    }) => {
        if (finishIndex === "last") {
            finishIndex = list.length - 1;
        }

        const reordered = reorder({ list, startIndex, finishIndex });
        return applyOptimisticOrder(list, reordered);
    };

    const reorderItemsWithEdge = <TValue extends TColumnModel | TRowModel>({
        list,
        startIndex,
        indexOfTarget,
        closestEdgeOfTarget,
    }: {
        list: TValue[];
        startIndex: number;
        indexOfTarget: number;
        closestEdgeOfTarget: Edge | null;
    }) => {
        const reordered = reorderWithEdge({
            axis: "vertical",
            list,
            startIndex,
            indexOfTarget,
            closestEdgeOfTarget,
        });
        return applyOptimisticOrder(list, reordered);
    };

    const moveRow = ({
        draggingRow,
        sourceColumn,
        destinationColumn,
        targetIndex,
    }: {
        draggingRow: TRowModel;
        sourceColumn: TColumnModel;
        destinationColumn: TColumnModel;
        targetIndex: number | "last";
    }) => {
        const updatedCards: Record<string, [number, string | null]> = {};
        let lastIndex = -1;
        Object.values(rowsMap).forEach((row) => {
            if (row[columnKeyInRow] === sourceColumn.uid && row.order > draggingRow.order) {
                updatedCards[row.uid] = [row.order, null];
                row.order -= 1;
                return;
            }

            if (row[columnKeyInRow] !== destinationColumn.uid) {
                return;
            }

            if (targetIndex === "last") {
                lastIndex = Math.max(lastIndex, row.order);
                return;
            }

            if (row.order >= targetIndex) {
                updatedCards[row.uid] = [row.order, null];
                row.order += 1;
            }
        });

        updatedCards[draggingRow.uid] = [draggingRow.order, draggingRow[columnKeyInRow] as string | null];
        draggingRow.order = targetIndex === "last" ? lastIndex + 1 : targetIndex;
        draggingRow[columnKeyInRow] = destinationColumn.uid as any;

        const undo = () => {
            Object.entries(updatedCards).forEach(([rowUID, [order, columnUID]]) => {
                const row = rowsMap[rowUID];
                if (!row) {
                    return;
                }

                row.order = order;
                if (columnUID) {
                    row[columnKeyInRow] = columnUID as any;
                }
            });
        };

        return {
            undo,
        };
    };

    return combine(
        monitorForElements({
            canMonitor: isDraggingARow,
            onDrop({ source, location }) {
                const dragging = source.data;
                if (!isRowData(dragging)) {
                    return;
                }

                const innerMost = location.current.dropTargets[0];
                if (!innerMost) {
                    return;
                }

                const dropTargetData = innerMost.data;
                const homeColumnIndex = columns.findIndex((column) => column.uid === dragging.row[columnKeyInRow]);
                const home = columns[homeColumnIndex];
                if (!home) {
                    return;
                }

                const rowIndexInHome = dragging.row.order;

                // dropping on a row
                if (isRowDroppableTargetData(dropTargetData)) {
                    const destinationColumnIndex = columns.findIndex((column) => column.uid === dropTargetData.row[columnKeyInRow]);
                    const destination = columns[destinationColumnIndex];
                    // reordering in home column
                    if (home === destination) {
                        const rowFinishIndex = dropTargetData.row.order;
                        // no change needed
                        if (rowIndexInHome === rowFinishIndex) {
                            return;
                        }

                        const closestEdge = extractClosestEdge(dropTargetData);
                        if (!canReorderByClosestEdge({ sourceIndex: rowIndexInHome, targetIndex: rowFinishIndex, closestEdge })) {
                            return;
                        }

                        const { undo } = reorderItemsWithEdge({
                            list: getRowsByColumn(home),
                            startIndex: rowIndexInHome,
                            indexOfTarget: rowFinishIndex,
                            closestEdgeOfTarget: closestEdge,
                        });

                        changeRowOrder({ rowUID: dragging.row.uid, order: dragging.row.order, undo });
                        return;
                    }

                    // moving row from one column to another

                    // unable to find destination
                    if (!destination) {
                        return;
                    }

                    const indexOfTarget = rowsMap[dropTargetData.row.uid].order;
                    const closestEdge = extractClosestEdge(dropTargetData);
                    const finalIndex = closestEdge === "bottom" ? indexOfTarget + 1 : indexOfTarget;

                    if (
                        home === destination &&
                        (rowIndexInHome === finalIndex ||
                            !canReorderByClosestEdge({ sourceIndex: rowIndexInHome, targetIndex: indexOfTarget, closestEdge }))
                    ) {
                        return;
                    }

                    const { undo } = moveRow({
                        draggingRow: dragging.row,
                        sourceColumn: home,
                        destinationColumn: destination,
                        targetIndex: finalIndex,
                    });

                    changeRowOrder({ rowUID: dragging.row.uid, order: dragging.row.order, parentUID: destination.uid, undo });
                    return;
                }

                // dropping onto a column, but not onto a row
                if (isColumnData(dropTargetData)) {
                    const destinationColumnIndex = columns.findIndex((column) => column.uid === dropTargetData.column.uid);
                    const destination = columns[destinationColumnIndex];

                    if (!destination) {
                        return;
                    }

                    // dropping on home
                    if (home === destination) {
                        const { undo } = reorderItems({
                            list: getRowsByColumn(home),
                            startIndex: rowIndexInHome,
                            finishIndex: "last",
                        });

                        changeRowOrder({ rowUID: dragging.row.uid, order: dragging.row.order, undo });
                        return;
                    }

                    // move row from home to another column
                    const { undo } = moveRow({
                        draggingRow: dragging.row,
                        sourceColumn: home,
                        destinationColumn: destination,
                        targetIndex: "last",
                    });

                    changeRowOrder({ rowUID: dragging.row.uid, order: dragging.row.order, parentUID: destination.uid, undo });
                    return;
                }
            },
        }),
        monitorForElements({
            canMonitor: isDraggingAColumn,
            onDrop({ source, location }) {
                const dragging = source.data;
                if (!isColumnData(dragging)) {
                    return;
                }

                const innerMost = location.current.dropTargets[0];
                if (!innerMost) {
                    return;
                }

                const dropTargetData = innerMost.data;
                const homeIndex = dragging.column.order;

                if (isColumnDroppableTargetData(dropTargetData)) {
                    const indexOfTarget = dropTargetData.column.order;
                    const closestEdge = extractClosestEdge(dropTargetData);

                    if (isIndicator && !canReorderByClosestEdge({ sourceIndex: homeIndex, targetIndex: indexOfTarget, closestEdge })) {
                        return;
                    }

                    if (homeIndex === indexOfTarget) {
                        return;
                    }

                    const { undo } = reorderItems({
                        list: columns,
                        startIndex: homeIndex,
                        finishIndex: indexOfTarget,
                    });

                    changeColumnOrder({ columnUID: dragging.column.uid, order: dragging.column.order, undo });
                    return;
                }

                if (!isColumnData(dropTargetData)) {
                    return;
                }

                const destinationIndex = dropTargetData.column.order;
                if (homeIndex === destinationIndex) {
                    return;
                }

                const { undo } = reorderItems({
                    list: columns,
                    startIndex: homeIndex,
                    finishIndex: destinationIndex,
                });

                changeColumnOrder({ columnUID: dragging.column.uid, order: dragging.column.order, undo });
            },
        }),
        autoScrollForElements({
            canScroll({ source }) {
                if (!settings.isOverElementAutoScrollEnabled) {
                    return false;
                }

                return isDraggingARow({ source }) || isDraggingAColumn({ source });
            },
            getConfiguration: () => ({ maxScrollSpeed: settings.rootScrollSpeed }),
            element: scrollable,
        }),
        unsafeOverflowAutoScrollForElements({
            element: scrollable,
            getConfiguration: () => ({ maxScrollSpeed: settings.rootScrollSpeed }),
            canScroll({ source }) {
                if (!settings.isOverElementAutoScrollEnabled) {
                    return false;
                }

                if (!settings.isOverflowScrollingEnabled) {
                    return false;
                }

                return isDraggingARow({ source }) || isDraggingAColumn({ source });
            },
            getOverflow() {
                return {
                    forLeftEdge: {
                        top: 1000,
                        left: 1000,
                        bottom: 1000,
                    },
                    forRightEdge: {
                        top: 1000,
                        right: 1000,
                        bottom: 1000,
                    },
                };
            },
        })
    );
};

export default createDndRootEvents;
