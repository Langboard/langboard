import { useEffect, useRef, forwardRef, type ButtonHTMLAttributes } from "react";
import { dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { BOARD_DND_SYMBOL_SET } from "@/pages/BoardPage/components/board/BoardConstants";
import { BOARD_CARD_ATTACHMENT_DND_SYMBOL_SET } from "@/pages/BoardPage/components/card/attachment/BoardCardAttachmentConstants";
import { ProjectCard, ProjectCardAttachment } from "@/core/models";
import type { TransferApp, TransferSource } from "./AppTransferDialog";

export interface TransferTarget extends TransferApp { source?: TransferSource }
const TransferRailButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
    transfer?: TransferApp; onTransfer: (target: TransferTarget) => void;
}>(function TransferRailButton({ transfer, onTransfer, ...props }, forwardedRef) {
    const ref = useRef<HTMLButtonElement>(null);
    useEffect(() => {
        if (!transfer?.enabled || !ref.current) return;
        const source = (data: Record<string | symbol, unknown>): TransferSource | undefined => {
            const row = data.row;
            if (data[BOARD_DND_SYMBOL_SET.row] === true && row instanceof ProjectCard.Model && row.project_uid === transfer.projectUID)
                return { cardUID: row.uid };
            if (data[BOARD_CARD_ATTACHMENT_DND_SYMBOL_SET.row] === true && row instanceof ProjectCardAttachment.Model)
                return { cardUID: row.card_uid, attachmentUID: row.uid };
            return undefined;
        };
        return dropTargetForElements({ element: ref.current, canDrop: ({ source: item }) => !!source(item.data),
            getDropEffect: () => "copy", onDrop: ({ source: item }) => {
                const identity = source(item.data);
                if (identity) onTransfer({ ...transfer, source: identity });
            } });
    }, [transfer, onTransfer]);
    return <button {...props} ref={(node) => {
        ref.current = node;
        if (typeof forwardedRef === "function") forwardedRef(node);
        else if (forwardedRef) forwardedRef.current = node;
    }} />;
});
export default TransferRailButton;
