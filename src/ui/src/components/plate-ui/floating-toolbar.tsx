/* eslint-disable @/max-len */
"use client";

import { cn } from "@/core/utils/ComponentUtils";
import { type FloatingToolbarState, flip, offset, useFloatingToolbar, useFloatingToolbarState } from "@platejs/floating";
import { KEYS } from "platejs";
import { useComposedRef, useEditorId, useEditorReadOnly, useEventEditorValue, usePluginOption } from "platejs/react";
import { Toolbar } from "@/components/plate-ui/toolbar";

type FloatingToolbarProps = React.ComponentProps<typeof Toolbar> & {
    state?: FloatingToolbarState;
};

export function FloatingToolbar({ children, className, state, ...props }: FloatingToolbarProps) {
    const readOnly = useEditorReadOnly();
    const editorId = useEditorId();
    const focusedEditorId = useEventEditorValue("focus");
    const isFloatingLinkOpen = !!usePluginOption({ key: KEYS.link }, "mode");
    const isAIChatOpen = usePluginOption({ key: KEYS.aiChat }, "open");

    if (readOnly) {
        return null;
    }

    const floatingToolbarState = useFloatingToolbarState({
        editorId,
        focusedEditorId,
        hideToolbar: isFloatingLinkOpen || isAIChatOpen,
        ...state,
        floatingOptions: {
            middleware: [
                offset(12),
                flip({
                    fallbackPlacements: ["top-start", "top-end", "bottom-start", "bottom-end"],
                    padding: 12,
                }),
            ],
            placement: "top",
            ...state?.floatingOptions,
        },
    });

    const { clickOutsideRef, hidden, props: rootProps, ref: floatingRef } = useFloatingToolbar(floatingToolbarState);

    const ref = useComposedRef<HTMLDivElement>(props.ref, floatingRef);

    if (hidden) return null;

    return (
        <div ref={clickOutsideRef}>
            <Toolbar
                ref={ref}
                className={cn(
                    "absolute z-50 overflow-x-auto whitespace-nowrap rounded-md border bg-popover p-1 opacity-100 shadow-md scrollbar-hide print:hidden",
                    "max-w-[80vw]"
                )}
                {...rootProps}
                {...props}
            >
                {children}
            </Toolbar>
        </div>
    );
}
