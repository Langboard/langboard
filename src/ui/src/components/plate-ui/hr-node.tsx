"use client";

import type { PlateElementProps } from "platejs/react";
import { PlateElement, useEditorRef, useFocused, useReadOnly, useSelected } from "platejs/react";
import { insertParagraphBesideBlock } from "@/components/plate-ui/block-gap-insertion";
import { cn } from "@/core/utils/ComponentUtils";
import type { MouseEvent } from "react";

export function HrElement(props: PlateElementProps) {
    const editor = useEditorRef();
    const readOnly = useReadOnly();
    const selected = useSelected();
    const focused = useFocused();
    const insertParagraphAfter = (event: MouseEvent<HTMLDivElement>) => {
        if (readOnly) {
            return;
        }

        const rect = event.currentTarget.getBoundingClientRect();
        const isBelowLine = event.clientY > rect.top + rect.height / 2;
        if (!isBelowLine) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        insertParagraphBesideBlock(editor, props.element, "after");
    };

    return (
        <PlateElement {...props}>
            <div className="py-6" contentEditable={false} onMouseDown={insertParagraphAfter}>
                <hr
                    className={cn(
                        "h-0.5 rounded-sm border-none bg-muted bg-clip-content",
                        selected && focused && "ring-2 ring-ring ring-offset-2",
                        !readOnly && "cursor-pointer"
                    )}
                />
            </div>
            {props.children}
        </PlateElement>
    );
}
