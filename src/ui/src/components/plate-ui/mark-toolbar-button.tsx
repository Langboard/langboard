"use client";

import * as React from "react";
import { useEditorReadOnly, useEditorRef, useEditorSelector } from "platejs/react";
import { ToolbarButton } from "@/components/plate-ui/toolbar";

export function MarkToolbarButton({
    clear,
    nodeType,
    ...props
}: React.ComponentProps<typeof ToolbarButton> & {
    nodeType: string;
    clear?: string[] | string;
}) {
    const editor = useEditorRef();
    const readOnly = useEditorReadOnly();
    const hasSelection = useEditorSelector((currentEditor) => !!currentEditor.selection, []);
    const pressed = useEditorSelector(
        (currentEditor) => {
            if (!currentEditor.selection) {
                return false;
            }

            try {
                return !!currentEditor.api.hasMark(nodeType);
            } catch {
                return false;
            }
        },
        [nodeType]
    );

    const handleClick = React.useCallback(() => {
        if (readOnly || !hasSelection) {
            return;
        }

        editor.tf.toggleMark(nodeType, clear ? { remove: clear } : undefined);
    }, [clear, editor, hasSelection, nodeType, readOnly]);

    const handleMouseDown = React.useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
        e.preventDefault();
    }, []);

    return <ToolbarButton {...props} pressed={pressed} disabled={readOnly || !hasSelection} onClick={handleClick} onMouseDown={handleMouseDown} />;
}
