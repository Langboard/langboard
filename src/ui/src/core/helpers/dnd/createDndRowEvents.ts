import { draggable as draggableFn, dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { preserveOffsetOnSource } from "@atlaskit/pragmatic-drag-and-drop/element/preserve-offset-on-source";
import { setCustomNativeDragPreview } from "@atlaskit/pragmatic-drag-and-drop/element/set-custom-native-drag-preview";
import invariant from "tiny-invariant";
import { attachClosestEdge, extractClosestEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { TRowDroppableTargetData, TRowData, TRowState, TColumnRowSymbolSet } from "@/core/helpers/dnd/types";
import createDndColumnRowDataHelper from "@/core/helpers/dnd/createDndColumnRowDataHelper";
import { TOrderableModel, TOrderableModelName } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";

export interface ICreateDndRowEventsProps<TRowModel extends TOrderableModel<TOrderableModelName>> {
    row: TRowModel;
    symbolSet: TColumnRowSymbolSet;
    draggable: HTMLElement;
    dropTarget: HTMLElement;
    canDropRow?: (row: TRowModel) => boolean;
    setState: React.Dispatch<React.SetStateAction<TRowState>>;
    renderPreview: Parameters<typeof setCustomNativeDragPreview>[0]["render"];
}

export const ROW_IDLE = { type: "idle" } satisfies TRowState;

const createDndRowEvents = <TRowModel extends TOrderableModel<TOrderableModelName>>(props: ICreateDndRowEventsProps<TRowModel>) => {
    type TRowModelData = TRowData<TRowModel>;
    type TRowDroppableModelData = TRowDroppableTargetData<TRowModel>;

    const { row, symbolSet, draggable, dropTarget, setState, renderPreview } = props;

    const getRowData = (context: TRowModelData): TRowModelData => {
        return {
            [symbolSet.row]: true,
            rect: context.rect,
            row: context.row,
        };
    };

    const getRowDroppableTargetData = (context: TRowDroppableModelData): TRowDroppableModelData => {
        return {
            [symbolSet.rowDroppable]: true,
            row: context.row,
        };
    };

    const { isRowData, shouldHideIndicator } = createDndColumnRowDataHelper<TOrderableModel<TOrderableModelName>, TRowModel>({
        symbolSet,
    });

    return combine(
        draggableFn({
            element: draggable,
            getInitialData: ({ element }) => getRowData({ row, rect: element.getBoundingClientRect() }),
            onGenerateDragPreview({ nativeSetDragImage, location, source }) {
                const data = source.data;
                invariant(isRowData(data));
                setCustomNativeDragPreview({
                    nativeSetDragImage,
                    getOffset: preserveOffsetOnSource({ element: draggable, input: location.current.input }),
                    render: renderPreview,
                });
            },
            onDragStart() {
                setState({ type: "is-dragging" });
            },
            onDrop() {
                setState(ROW_IDLE);
            },
        }),
        dropTargetForElements({
            element: dropTarget,
            getIsSticky: () => true,
            canDrop: ({ source }) => isRowData(source.data) && (props.canDropRow?.(source.data.row) ?? true),
            getData: ({ element, input }) => {
                const data = getRowDroppableTargetData({ row });
                return attachClosestEdge(data, { element, input, allowedEdges: ["top", "bottom"] });
            },
            onDragEnter({ source, self }) {
                if (!isRowData(source.data)) {
                    return;
                }
                if (source.data.row.uid === row.uid) {
                    return;
                }
                const closestEdge = extractClosestEdge(self.data);
                if (!closestEdge) {
                    return;
                }

                if (shouldHideIndicator(row.order, source.data.row.order, closestEdge)) {
                    // If the row is before or after the source, we don't want to show the "is-over" state.
                    setState(ROW_IDLE);
                    return;
                }

                setState({ type: "is-over", dragging: source.data.rect, closestEdge });
            },
            onDrag({ source, self }) {
                if (!isRowData(source.data) || source.data.row.uid === row.uid) {
                    return;
                }

                const closestEdge = extractClosestEdge(self.data);
                if (!closestEdge) {
                    return;
                }

                if (shouldHideIndicator(row.order, source.data.row.order, closestEdge)) {
                    // If the row is before or after the source, we don't want to show the "is-over" state.
                    setState(ROW_IDLE);
                    return;
                }

                // optimization - Don't update react state if we don't need to.
                const proposed: TRowState = { type: "is-over", dragging: source.data.rect, closestEdge };
                setState((current) => {
                    if (Utils.Object.isShallowEqual(proposed, current)) {
                        return current;
                    }
                    return proposed;
                });
            },
            onDragLeave({ source }) {
                if (!isRowData(source.data)) {
                    return;
                }
                if (source.data.row.uid === row.uid) {
                    setState({ type: "is-dragging-and-left-self" });
                    return;
                }
                setState(ROW_IDLE);
            },
            onDrop() {
                setState(ROW_IDLE);
            },
        })
    );
};

export default createDndRowEvents;
