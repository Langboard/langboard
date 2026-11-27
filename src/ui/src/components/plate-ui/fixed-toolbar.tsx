/* eslint-disable @/max-len */
"use client";

import { cn } from "@/core/utils/ComponentUtils";
import { Toolbar } from "@/components/plate-ui/toolbar";
import { useEditorData } from "@/core/providers/EditorDataProvider";
import { EEditorType } from "@langboard/core/constants";
import { useEditorReadOnly } from "platejs/react";

export function FixedToolbar(props: React.ComponentProps<typeof Toolbar>) {
    const readOnly = useEditorReadOnly();
    const { editorType } = useEditorData();

    if (readOnly) {
        return null;
    }

    return (
        <Toolbar
            {...props}
            className={cn(
                "supports-backdrop-blur:bg-background/60 sticky left-0 z-50 w-full justify-between overflow-x-auto rounded-t-lg border-b border-b-border bg-background/95 p-1 backdrop-blur-sm",
                editorType === EEditorType.CardDescription ? "top-0 z-[80]" : "top-0",
                props.className
            )}
        />
    );
}
