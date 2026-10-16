import { Edge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/dist/types/types";
import { reorder } from "@atlaskit/pragmatic-drag-and-drop/reorder";
import { getReorderDestinationIndex } from "@atlaskit/pragmatic-drag-and-drop-hitbox/util/get-reorder-destination-index";
import { monitorForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { extractClosestEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import createDndSingleDataHelper from "@/core/helpers/dnd/createDndSingleDataHelper";
import { TSingleRowSettings, TSingleSymbolSet } from "@/core/helpers/dnd/types";
import { canReorderByClosestEdge } from "@/core/helpers/dnd/utils";
import { CleanupFn } from "@atlaskit/pragmatic-drag-and-drop/dist/types/internal-types";
import { TOrderableModel, TOrderableModelName } from "@/core/models/ModelRegistry";
import { unsafeOverflowAutoScrollForElements } from "@/core/helpers/dnd/auto-scroll/entry-point/unsafe-overflow/element";
import { autoScrollForElements } from "@/core/helpers/dnd/auto-scroll/entry-point/element";

export interface ICreateDndSingleRootEventsProps<TRowModel extends TOrderableModel<TOrderableModelName>> {
    rowsMap: Record<string, TRowModel>;
    symbolSet: TSingleSymbolSet;
    settings?: TSingleRowSettings;
    scrollable?: HTMLElement;
    isHorizontal?: bool;
    changeOrder: (context: { rowUID: string; order: number; undo: () => void }) => void;
}

const createDndSingleRootEvents = <TRowModel extends TOrderableModel<TOrderableModelName>>(props: ICreateDndSingleRootEventsProps<TRowModel>) => {
    const { rowsMap, symbolSet, settings, scrollable, isHorizontal, changeOrder } = props;
    const { isRowData, isDraggingARow } = createDndSingleDataHelper<TRowModel>({ symbolSet });

    const reorderItem = ({
        startIndex,
        indexOfTarget,
        closestEdgeOfTarget,
    }: {
        startIndex: number;
        indexOfTarget: number;
        closestEdgeOfTarget: Edge | null;
    }) => {
        const finishIndex = getReorderDestinationIndex({
            startIndex,
            closestEdgeOfTarget,
            indexOfTarget,
            axis: isHorizontal ? "horizontal" : "vertical",
        });

        if (
            finishIndex === startIndex ||
            !canReorderByClosestEdge({ sourceIndex: startIndex, targetIndex: indexOfTarget, closestEdge: closestEdgeOfTarget, isHorizontal })
        ) {
            // If there would be no change, we skip the update
            return {};
        }

        const rows = Object.values(rowsMap).sort((a, b) => a.order - b.order);

        const reordered = reorder({ list: rows, startIndex, finishIndex });
        reordered.forEach((item, index) => {
            item.order = index;
        });

        const undo = () => {
            const undoRedorered = reorder({ list: rows, startIndex: finishIndex, finishIndex: startIndex });
            undoRedorered.forEach((item, index) => {
                item.order = index;
            });
        };

        return { undo };
    };

    const autoScrollCleanups: Record<string, CleanupFn> = {};
    if (scrollable && settings) {
        autoScrollCleanups.autoScrollForElements = autoScrollForElements({
            canScroll({ source }) {
                if (!settings.isOverElementAutoScrollEnabled) {
                    return false;
                }

                return isDraggingARow({ source });
            },
            getConfiguration: () => ({ maxScrollSpeed: settings.rootScrollSpeed }),
            element: scrollable,
        });

        autoScrollCleanups.unsafeOverflowAutoScrollForElements = unsafeOverflowAutoScrollForElements({
            element: scrollable,
            getConfiguration: () => ({ maxScrollSpeed: settings.rootScrollSpeed }),
            canScroll({ source }) {
                if (!settings.isOverElementAutoScrollEnabled) {
                    return false;
                }

                if (!settings.isOverflowScrollingEnabled) {
                    return false;
                }

                return isDraggingARow({ source });
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
        });
    }

    return combine(
        monitorForElements({
            canMonitor({ source }) {
                return isRowData(source.data);
            },
            onDrop({ location, source }) {
                const target = location.current.dropTargets[0];
                if (!target) {
                    return;
                }

                const sourceData = source.data;
                const targetData = target.data;
                if (!isRowData(sourceData) || !isRowData(targetData)) {
                    return;
                }

                const indexOfTarget = rowsMap[targetData.row.uid]?.order ?? -1;
                if (indexOfTarget < 0) {
                    return;
                }

                const closestEdgeOfTarget = extractClosestEdge(targetData);

                const { undo } = reorderItem({
                    startIndex: sourceData.row.order,
                    indexOfTarget,
                    closestEdgeOfTarget,
                });

                if (!undo) {
                    return;
                }

                changeOrder({
                    rowUID: sourceData.row.uid,
                    order: sourceData.row.order,
                    undo,
                });
            },
        }),
        ...Object.values(autoScrollCleanups)
    );
};

export default createDndSingleRootEvents;
