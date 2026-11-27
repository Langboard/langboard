import { Command as CommandPrimitive } from "cmdk";
import React from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Command from "@/components/base/Command";
import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";
import Popover from "@/components/base/Popover";
import Skeleton from "@/components/base/Skeleton";
import { cn } from "@/core/utils/ComponentUtils";
import IconComponent from "@/components/base/IconComponent";
import Flex from "@/components/base/Flex";
import Box from "@/components/base/Box";
import { Utils } from "@langboard/core/utils";
import Floating from "@/components/base/Floating";
import { ICollaborativeTextCursor } from "@/components/Collaborative/useCollaborativeText";

export interface IAutorCompleteProps {
    selectedValue?: string;
    onValueChange: (value: string) => void;
    items: { value: string; label: string }[];
    isLoading?: bool;
    emptyMessage: string;
    placeholder: string;
    disabled?: bool;
    required?: bool;
    className?: string;
    collaborativeCursors?: ICollaborativeTextCursor[];
    onSelectionChange?: (selectionStart: number, selectionEnd: number) => void;
}

function AutoComplete({
    selectedValue,
    onValueChange,
    items,
    isLoading,
    emptyMessage,
    placeholder,
    disabled,
    required,
    className,
    collaborativeCursors = [],
    onSelectionChange,
}: IAutorCompleteProps) {
    const id = useRef(Utils.String.Token.shortUUID());
    const inputRef = useRef<HTMLInputElement>(null);
    const [open, setOpen] = useState(false);
    const [currentValue, setCurrentValue] = useState(selectedValue);

    useEffect(() => {
        setCurrentValue(selectedValue);
    }, [selectedValue]);

    const labels = useMemo(
        () =>
            items.reduce(
                (acc, item) => {
                    acc[item.value] = item.label;
                    return acc;
                },
                {} as Record<string, string>
            ),
        [items]
    );

    const changeValue = (inputValue: string) => {
        onValueChange(inputValue);
        setCurrentValue(inputValue);
    };

    const updateLocalSelection = useCallback(() => {
        const input = inputRef.current;
        if (!input || !onSelectionChange) {
            return;
        }

        onSelectionChange(input.selectionStart ?? 0, input.selectionEnd ?? input.selectionStart ?? 0);
    }, [onSelectionChange]);

    const onSelectItem = (inputValue: string) => {
        changeValue(inputValue);
        setOpen(false);
    };

    return (
        <Flex items="center" className={className}>
            <Popover.Root open={open} onOpenChange={setOpen}>
                <Command.Root
                    className="overflow-visible"
                    shouldFilter={false}
                    onKeyDown={(e) => {
                        if (!open) {
                            e.preventDefault();
                            setOpen(true);
                        }
                    }}
                >
                    <Popover.Trigger asChild>
                        <CommandPrimitive.Input
                            asChild
                            id={id.current}
                            value={currentValue ? (labels[currentValue] ?? currentValue) : currentValue}
                            onValueChange={changeValue}
                            onKeyDown={(e) => {
                                if (e.key !== "Escape") {
                                    if (!open) {
                                        setOpen(true);
                                    }
                                    return;
                                }

                                setOpen(false);
                            }}
                        >
                            <CollaborativeAutoCompleteInput
                                ref={inputRef}
                                label={placeholder}
                                required={required}
                                autoComplete="off"
                                disabled={disabled}
                                remoteCursors={collaborativeCursors}
                                value={currentValue ? (labels[currentValue] ?? currentValue) : currentValue}
                                onClick={updateLocalSelection}
                                onFocus={updateLocalSelection}
                                onKeyUp={updateLocalSelection}
                                onMouseUp={updateLocalSelection}
                                onSelect={updateLocalSelection}
                            />
                        </CommandPrimitive.Input>
                    </Popover.Trigger>
                    {!open && <Command.List aria-hidden="true" className="hidden" />}
                    <Popover.Content
                        asChild
                        onOpenAutoFocus={(e) => e.preventDefault()}
                        onInteractOutside={(e) => {
                            if (!Utils.Type.isElement(e.target, "input")) {
                                return;
                            }

                            if (e.target.hasAttribute("cmdk-input") && e.target.id === id.current) {
                                e.preventDefault();
                            }
                        }}
                        className="w-[--radix-popover-trigger-width] p-0"
                    >
                        <Command.List>
                            {isLoading && (
                                <CommandPrimitive.Loading>
                                    <Box p="1">
                                        <Skeleton h="6" w="full" />
                                    </Box>
                                </CommandPrimitive.Loading>
                            )}
                            {items.length > 0 && !isLoading ? (
                                <Command.Group>
                                    {items.map((option) => (
                                        <Command.Item
                                            key={option.value}
                                            value={option.value}
                                            onMouseDown={(e) => e.preventDefault()}
                                            onSelect={onSelectItem}
                                        >
                                            <IconComponent
                                                icon="check"
                                                size="4"
                                                className={cn("mr-2", currentValue === option.value ? "opacity-100" : "opacity-0")}
                                            />
                                            {option.label}
                                        </Command.Item>
                                    ))}
                                </Command.Group>
                            ) : null}
                            {!isLoading ? <Command.Empty>{emptyMessage ?? "No items."}</Command.Empty> : null}
                        </Command.List>
                    </Popover.Content>
                </Command.Root>
            </Popover.Root>
        </Flex>
    );
}

export default AutoComplete;

interface ICursorOverlayPosition {
    caretHeight: number;
    caretLeft: number;
    highlightHeight: number;
    highlightLeft: number;
    highlightWidth: number;
    top: number;
}

const createMeasureMarker = () => {
    const marker = document.createElement("span");
    marker.style.display = "inline-block";
    marker.style.width = "0";
    marker.style.padding = "0";
    marker.style.margin = "0";
    marker.style.border = "0";
    marker.style.overflow = "hidden";
    marker.textContent = "\u200b";
    return marker;
};

interface ICollaborativeAutoCompleteInputProps extends React.ComponentProps<typeof Floating.LabelInput> {
    remoteCursors: ICollaborativeTextCursor[];
}

const CollaborativeAutoCompleteInput = React.forwardRef<HTMLInputElement, ICollaborativeAutoCompleteInputProps>(
    ({ remoteCursors, value, ...props }, ref) => {
        const inputRef = useRef<HTMLInputElement>(null);
        const [cursorPositions, setCursorPositions] = useState<Record<number, ICursorOverlayPosition>>({});

        useLayoutEffect(() => {
            const input = inputRef.current;
            if (!input || !remoteCursors.length) {
                setCursorPositions({});
                return;
            }

            const inputValue = Utils.Type.isString(value) ? value : "";
            const styles = window.getComputedStyle(input);
            const mirror = document.createElement("div");
            const trackedStyles = [
                "boxSizing",
                "fontFamily",
                "fontSize",
                "fontWeight",
                "letterSpacing",
                "lineHeight",
                "paddingBottom",
                "paddingLeft",
                "paddingRight",
                "paddingTop",
            ] as const;

            mirror.style.position = "absolute";
            mirror.style.visibility = "hidden";
            mirror.style.overflow = "hidden";
            mirror.style.top = "0";
            mirror.style.left = "-9999px";
            mirror.style.height = `${input.offsetHeight}px`;
            mirror.style.whiteSpace = "pre";
            trackedStyles.forEach((styleName) => {
                mirror.style[styleName] = styles[styleName];
            });

            const paddingTop = Number.parseFloat(styles.paddingTop) || 0;
            const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0;
            const fontSize = Number.parseFloat(styles.fontSize) || 16;
            const lineHeight = Number.parseFloat(styles.lineHeight) || fontSize * 1.2;
            const contentHeight = input.clientHeight - paddingTop - paddingBottom;
            const top = paddingTop + Math.max((contentHeight - lineHeight) / 2, 0);
            const nextPositions: Record<number, ICursorOverlayPosition> = {};
            document.body.appendChild(mirror);

            remoteCursors.forEach((cursor) => {
                const selectionStart = Math.min(cursor.selectionStart, cursor.selectionEnd);
                const selectionEnd = Math.max(cursor.selectionStart, cursor.selectionEnd);
                const startMarker = createMeasureMarker();
                const endMarker = createMeasureMarker();

                mirror.textContent = inputValue.slice(0, selectionStart);
                mirror.appendChild(startMarker);
                mirror.appendChild(document.createTextNode(inputValue.slice(selectionStart, selectionEnd)));
                mirror.appendChild(endMarker);

                const startLeft = startMarker.offsetLeft - input.scrollLeft;
                const endLeft = endMarker.offsetLeft - input.scrollLeft;
                nextPositions[cursor.clientID] = {
                    caretHeight: lineHeight,
                    caretLeft: endLeft,
                    highlightHeight: lineHeight,
                    highlightLeft: startLeft,
                    highlightWidth: Math.max(endLeft - startLeft, 0),
                    top,
                };
            });

            document.body.removeChild(mirror);
            setCursorPositions(nextPositions);
        }, [remoteCursors, value]);

        return (
            <div className="relative w-full">
                <Floating.LabelInput
                    {...props}
                    ref={(node) => {
                        inputRef.current = node;

                        if (!ref) {
                            return;
                        }

                        if (typeof ref === "function") {
                            ref(node);
                            return;
                        }

                        ref.current = node;
                    }}
                    value={value}
                />
                <RemoteCursors cursors={remoteCursors} positions={cursorPositions} />
            </div>
        );
    }
);

CollaborativeAutoCompleteInput.displayName = "CollaborativeAutoCompleteInput";

function RemoteCursors({ cursors, positions }: { cursors: ICollaborativeTextCursor[]; positions: Record<number, ICursorOverlayPosition> }) {
    return (
        <>
            {cursors.map((cursor) => {
                const position = positions[cursor.clientID];
                if (!position) {
                    return null;
                }

                return (
                    <React.Fragment key={cursor.clientID}>
                        {cursor.selectionStart !== cursor.selectionEnd && (
                            <span
                                className="pointer-events-none absolute z-[9] rounded-sm opacity-25"
                                style={{
                                    backgroundColor: cursor.color,
                                    height: position.highlightHeight,
                                    left: position.highlightLeft,
                                    top: position.top,
                                    width: position.highlightWidth,
                                }}
                            />
                        )}
                        <span
                            className="pointer-events-none absolute z-10 w-0.5"
                            style={{
                                backgroundColor: cursor.color,
                                height: position.caretHeight,
                                left: position.caretLeft,
                                top: position.top,
                            }}
                        >
                            <CollaborativeUserLabel
                                className="absolute left-0 top-0 -translate-y-full whitespace-nowrap"
                                color={cursor.color}
                                name={cursor.name}
                            />
                        </span>
                    </React.Fragment>
                );
            })}
        </>
    );
}
