/* eslint-disable @/max-len */
"use client";

import * as React from "react";
import type { Point, TElement } from "platejs";
import {
    type ComboboxItemProps,
    Combobox,
    ComboboxGroup,
    ComboboxGroupLabel,
    ComboboxItem,
    ComboboxPopover,
    ComboboxProvider,
    ComboboxRow,
    Portal,
    useComboboxContext,
    useComboboxStore,
    useStoreState,
} from "@ariakit/react";
import { filterWords } from "@platejs/combobox";
import { type UseComboboxInputResult, useComboboxInput, useHTMLInputCursorState } from "@platejs/combobox/react";
import { cva } from "class-variance-authority";
import { useComposedRef, useEditorContainerRef, useEditorRef } from "platejs/react";
import { useIsMobile } from "@/core/hooks/useIsMobile";
import { cn } from "@/core/utils/ComponentUtils";

type FilterFn = (item: { value: string; group?: string; keywords?: string[]; label?: string }, search: string) => bool;

interface InlineComboboxContextValue {
    mobileEditorAnchor: boolean;
    filter: FilterFn | false;
    inputProps: UseComboboxInputResult["props"];
    inputRef: React.RefObject<HTMLInputElement | null>;
    removeInput: UseComboboxInputResult["removeInput"];
    showTrigger: bool;
    trigger: string;
    setHasEmpty: (hasEmpty: bool) => void;
}

const InlineComboboxContext = React.createContext<InlineComboboxContextValue>(null as unknown as InlineComboboxContextValue);

const defaultFilter: FilterFn = ({ group, keywords = [], label, value }, search) => {
    const uniqueTerms = new Set([value, ...keywords, group, label].filter(Boolean));

    return Array.from(uniqueTerms).some((keyword) => filterWords(keyword!, search));
};

interface InlineComboboxProps {
    children: React.ReactNode;
    element: TElement;
    trigger: string;
    filter?: FilterFn | false;
    hideWhenNoValue?: bool;
    mobileEditorAnchor?: boolean;
    showTrigger?: bool;
    value?: string;
    setValue?: (value: string) => void;
}

const InlineCombobox = ({
    children,
    element,
    filter = defaultFilter,
    hideWhenNoValue = false,
    mobileEditorAnchor = false,
    setValue: setValueProp,
    showTrigger = true,
    trigger,
    value: valueProp,
}: InlineComboboxProps) => {
    const editor = useEditorRef();
    const isMobile = useIsMobile();
    const inputRef = React.useRef<HTMLInputElement>(null);
    const cursorState = useHTMLInputCursorState(inputRef);

    const [valueState, setValueState] = React.useState("");
    const hasValueProp = valueProp !== undefined;
    const value = hasValueProp ? valueProp : valueState;

    const setValue = React.useCallback(
        (newValue: string) => {
            setValueProp?.(newValue);

            if (!hasValueProp) {
                setValueState(newValue);
            }
        },
        [setValueProp, hasValueProp]
    );

    /**
     * Track the point just before the input element so we know where to
     * insertText if the combobox closes due to a selection change.
     */
    const insertPoint = React.useRef<Point | null>(null);

    React.useEffect(() => {
        const path = editor.api.findPath(element);

        if (!path) return;

        const point = editor.api.before(path);

        if (!point) return;

        const pointRef = editor.api.pointRef(point);
        insertPoint.current = pointRef.current;

        return () => {
            pointRef.unref();
        };
    }, [editor, element]);

    const { props: inputProps, removeInput } = useComboboxInput({
        cancelInputOnBlur: true,
        cursorState,
        ref: inputRef,
        onCancelInput: (cause) => {
            if (cause !== "backspace") {
                editor.tf.insertText(trigger + value, {
                    at: insertPoint?.current ?? undefined,
                });
            }
            if (cause === "arrowLeft" || cause === "arrowRight") {
                editor.tf.move({
                    distance: 1,
                    reverse: cause === "arrowLeft",
                });
            }
        },
    });

    const [hasEmpty, setHasEmpty] = React.useState(false);

    const contextValue: InlineComboboxContextValue = React.useMemo(
        () => ({
            filter,
            mobileEditorAnchor,
            inputProps,
            inputRef,
            removeInput,
            setHasEmpty,
            showTrigger,
            trigger,
        }),
        [trigger, showTrigger, filter, mobileEditorAnchor, inputProps, removeInput, setHasEmpty]
    );

    const store = useComboboxStore({
        placement: mobileEditorAnchor && isMobile ? "top-start" : "bottom-start",
        setValue: (newValue) => React.startTransition(() => setValue(newValue)),
    });

    const items = useStoreState(store, "items");

    /**
     * Filtering can remove the active option. Keep keyboard selection on a
     * currently registered item.
     */
    React.useLayoutEffect(() => {
        if (!items.some((item) => item.id === store.getState().activeId && !item.disabled)) {
            store.setActiveId(store.first());
        }
    }, [items, store]);

    return (
        <span contentEditable={false}>
            <ComboboxProvider open={(items.length > 0 || hasEmpty) && (!hideWhenNoValue || value.length > 0)} store={store}>
                <InlineComboboxContext.Provider value={contextValue}>{children}</InlineComboboxContext.Provider>
            </ComboboxProvider>
        </span>
    );
};

const InlineComboboxInput = React.forwardRef<HTMLInputElement, React.HTMLAttributes<HTMLInputElement>>(({ className, ...props }, propRef) => {
    const { inputProps, inputRef: contextRef, showTrigger, trigger } = React.useContext(InlineComboboxContext);

    const store = useComboboxContext()!;
    const value = useStoreState(store, "value");

    const ref = useComposedRef(propRef, contextRef);

    /**
     * To create an auto-resizing input, we render a visually hidden span
     * containing the input value and position the input element on top of it.
     * This works well for all cases except when input exceeds the width of the
     * container.
     */

    return (
        <>
            {showTrigger && trigger}

            <span className="relative inline-block min-h-[1lh] min-w-[1ch] align-baseline">
                <span className="invisible overflow-hidden text-nowrap" aria-hidden="true">
                    {value || "\u200B"}
                </span>

                <Combobox
                    ref={ref}
                    className={cn("absolute left-0 top-0 size-full bg-transparent outline-none", className)}
                    value={value}
                    autoSelect
                    {...inputProps}
                    {...props}
                    onKeyDownCapture={(event) => {
                        props.onKeyDownCapture?.(event);
                        // Ariakit proxies capture events to options before its bubbling IME guard.
                        // Keep the native composition commit while preventing premature selection.
                        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) event.stopPropagation();
                    }}
                />
            </span>
        </>
    );
});

InlineComboboxInput.displayName = "InlineComboboxInput";

const InlineComboboxContent: typeof ComboboxPopover = ({ className, ...props }) => {
    const { mobileEditorAnchor } = React.useContext(InlineComboboxContext);
    const containerRef = useEditorContainerRef();
    const isMobile = useIsMobile();
    const anchorEditor = mobileEditorAnchor && isMobile;
    const getAnchorRect = React.useCallback(() => containerRef.current?.getBoundingClientRect() ?? null, [containerRef]);
    // Reuse Ariakit's positioning and viewport sizing against the complete mobile editor.
    return (
        <Portal>
            <ComboboxPopover
                data-plate-combobox-content
                className={cn(
                    "z-[500] max-h-[288px] w-[300px] overflow-y-auto rounded-md border bg-popover shadow-md",
                    className,
                    anchorEditor && "my-0 max-h-[min(288px,var(--popover-available-height))] w-[var(--popover-anchor-width)] overscroll-contain"
                )}
                {...props}
                {...(anchorEditor ? { fixed: true, getAnchorRect, sameWidth: true, fitViewport: true, flip: false, gutter: 4, slide: false } : {})}
            />
        </Portal>
    );
};

const comboboxItemVariants = cva(
    "relative mx-1 flex h-[28px] items-center rounded-sm px-2 text-sm text-foreground outline-none select-none [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    {
        defaultVariants: {
            interactive: true,
        },
        variants: {
            interactive: {
                false: "",
                true: "cursor-pointer transition-colors hover:bg-accent hover:text-accent-foreground data-[active-item=true]:bg-accent data-[active-item=true]:text-accent-foreground",
            },
        },
    }
);

const InlineComboboxItem = ({
    className,
    focusEditor = true,
    group,
    keywords,
    label,
    onClick,
    ...props
}: {
    focusEditor?: bool;
    group?: string;
    keywords?: string[];
    label?: string;
} & ComboboxItemProps &
    Required<Pick<ComboboxItemProps, "value">>) => {
    const { value } = props;

    const { filter, removeInput } = React.useContext(InlineComboboxContext);

    const store = useComboboxContext()!;

    // Optimization: Do not subscribe to value if filter is false
    const search = filter && useStoreState(store, "value");

    const visible = React.useMemo(
        () => !filter || filter({ group, keywords, label, value }, search as string),
        [filter, group, keywords, label, value, search]
    );

    if (!visible) return null;

    return (
        <ComboboxItem
            className={cn(comboboxItemVariants(), className)}
            onClick={(event) => {
                removeInput(focusEditor);
                onClick?.(event);
            }}
            {...props}
        />
    );
};

const InlineComboboxEmpty = ({ children, className }: React.HTMLAttributes<HTMLDivElement>) => {
    const { setHasEmpty } = React.useContext(InlineComboboxContext);
    const store = useComboboxContext()!;
    const items = useStoreState(store, "items");

    React.useEffect(() => {
        setHasEmpty(true);

        return () => {
            setHasEmpty(false);
        };
    }, [setHasEmpty]);

    if (items.length > 0) return null;

    return <div className={cn(comboboxItemVariants({ interactive: false }), className)}>{children}</div>;
};

const InlineComboboxRow = ComboboxRow;

function InlineComboboxGroup({ className, ...props }: React.ComponentProps<typeof ComboboxGroup>) {
    return <ComboboxGroup {...props} className={cn("not-last:border-b hidden py-1.5 [&:has([role=option])]:block", className)} />;
}

function InlineComboboxGroupLabel({ className, ...props }: React.ComponentProps<typeof ComboboxGroupLabel>) {
    return <ComboboxGroupLabel {...props} className={cn("mb-2 mt-1.5 px-3 text-xs font-medium text-muted-foreground", className)} />;
}

export {
    InlineCombobox,
    InlineComboboxContent,
    InlineComboboxEmpty,
    InlineComboboxGroup,
    InlineComboboxGroupLabel,
    InlineComboboxInput,
    InlineComboboxItem,
    InlineComboboxRow,
};
