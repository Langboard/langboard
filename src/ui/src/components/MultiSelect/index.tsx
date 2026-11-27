/* eslint-disable @/max-len */
"use client";

import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";
import Badge from "@/components/base/Badge";
import Box from "@/components/base/Box";
import Command from "@/components/base/Command";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { useFloating, autoUpdate, offset, shift, limitShift, hide, flip, size } from "@floating-ui/react-dom";
import { Utils } from "@langboard/core/utils";

export interface IMultiSelectItem {
    label: string;
    value: string;
}

interface IBaseMultiSelectProps {
    selections: IMultiSelectItem[];
    placeholder?: string;
    selectedValue?: string[];
    onValueChange?: (value: string[]) => void;
    className?: string;
    badgeListClassName?: string;
    badgeClassName?: string;
    inputClassName?: string;
    listClassName?: string;
    noBadge?: bool;
    createValueText?: (value: string[]) => string;
    createBadgeWrapper?: (badge: React.JSX.Element, value: string) => React.JSX.Element | null;
    renderSelectableItem?: (item: IMultiSelectItem) => React.ReactNode;
    canCreateNew?: bool;
    validateCreatedNewValue?: (value: string) => bool;
    createNewCommandItemLabel?: ((values: string[]) => string) | ((value: string) => { label: string; value: string }[]);
    multipleSplitter?: string;
    isNewCommandItemMultiple?: bool;
    disabled?: bool;
}

interface IDisabledBadgeMultiSelectProps extends IBaseMultiSelectProps {
    noBadge: true;
    createValueText: (value: string[]) => string;
    createBadgeWrapper?: never;
}

interface IAllowedCreateNewMultiSelectProps extends IBaseMultiSelectProps {
    noBadge?: false;
    createValueText?: never;
    canCreateNew: true;
    validateCreatedNewValue: (value: string) => bool;
    createNewCommandItemLabel?: ((values: string[]) => string) | ((value: string) => { label: string; value: string }[]);
    multipleSplitter?: string;
    isNewCommandItemMultiple?: bool;
}

interface IAllowedCreateNewWithMultipleCommandItemMultiSelectProps extends IAllowedCreateNewMultiSelectProps {
    createNewCommandItemLabel: (value: string) => { label: string; value: string }[];
    isNewCommandItemMultiple: true;
}

export interface IAllowedCreateNewWithSingleCommandItemMultiSelectProps extends IAllowedCreateNewMultiSelectProps {
    createNewCommandItemLabel: (values: string[]) => string;
    isNewCommandItemMultiple?: false;
}

interface IDefaultMultiSelectProps extends IBaseMultiSelectProps {
    canCreateNew?: false;
    validateCreatedNewValue?: never;
    createNewCommandItemLabel?: never;
    multipleSplitter?: never;
}

export type TMultiSelectProps =
    | IDisabledBadgeMultiSelectProps
    | IAllowedCreateNewWithMultipleCommandItemMultiSelectProps
    | IAllowedCreateNewWithSingleCommandItemMultiSelectProps
    | IDefaultMultiSelectProps;

const MultiSelect = React.memo(
    ({
        selections,
        placeholder,
        selectedValue,
        onValueChange,
        className,
        badgeListClassName,
        badgeClassName,
        inputClassName,
        listClassName,
        noBadge,
        createValueText,
        createBadgeWrapper,
        renderSelectableItem,
        canCreateNew,
        validateCreatedNewValue,
        createNewCommandItemLabel,
        multipleSplitter = ",",
        isNewCommandItemMultiple,
        disabled,
    }: TMultiSelectProps) => {
        const [wrapper, setWrapper] = React.useState<HTMLDivElement | null>(null);
        const inputRef = React.useRef<HTMLInputElement>(null);
        const [open, setOpen] = React.useState(false);
        const [selected, setSelected] = React.useState(selectedValue ?? []);
        const badgeListRef = React.useRef<HTMLDivElement | null>(null);
        const setSelectedWithFireEvent = React.useCallback(
            (action: React.SetStateAction<string[]>) => {
                setSelected((prev) => {
                    const newSelected = Utils.Type.isFunction(action) ? action(prev) : action;
                    if (onValueChange) {
                        setTimeout(() => {
                            setInputValue("");
                            onValueChange(newSelected);
                            badgeListRef.current?.scrollTo({ top: badgeListRef.current.scrollHeight });
                        }, 0);
                    }
                    return newSelected;
                });
            },
            [setSelected, onValueChange]
        );
        const selectables = React.useMemo(
            () => selections.filter((selection) => !selected.includes(selection.value)),
            [selections, selectedValue, selected]
        );
        const [inputValue, setInputValue] = React.useState("");
        const handleUnselect = React.useCallback(
            (item: string) => {
                setSelectedWithFireEvent((prev) => prev.filter((selected) => selected !== item));
                setTimeout(() => {
                    inputRef.current?.focus();
                }, 0);
            },
            [setSelectedWithFireEvent]
        );
        const handleKeyDown = React.useCallback(
            (e: React.KeyboardEvent<HTMLDivElement>) => {
                const input = inputRef.current;
                if (!input) {
                    return;
                }

                if (e.key === "Delete" || e.key === "Backspace") {
                    if (input.value === "") {
                        setSelectedWithFireEvent((prev) => {
                            const newSelected = [...prev];
                            newSelected.pop();
                            return newSelected;
                        });
                    }
                }

                if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    input.blur();
                }
            },
            [setSelectedWithFireEvent]
        );
        const handleChangeInputValue = React.useCallback(
            (value: string) => {
                if (!canCreateNew || Math.abs(value.length - inputValue.length) === 1) {
                    setInputValue(value);
                    return;
                }

                const splitValues = value.split(multipleSplitter);
                if (splitValues.length === 1) {
                    setInputValue(value);
                    return;
                }

                const valuesCanBeAdded: string[] = [];
                for (let i = 0; i < splitValues.length; ++i) {
                    const trimmedValue = splitValues[i].trim();
                    if (!trimmedValue || !validateCreatedNewValue?.(trimmedValue) || selected.includes(trimmedValue)) {
                        continue;
                    }

                    valuesCanBeAdded.push(trimmedValue);
                }

                if (valuesCanBeAdded.length) {
                    setSelectedWithFireEvent((prev) => [...prev, ...valuesCanBeAdded]);
                }
            },
            [selected, inputValue, setSelectedWithFireEvent]
        );
        const parsedMultipleValues = React.useMemo(() => {
            if (!canCreateNew || !validateCreatedNewValue) {
                return [];
            }

            return inputValue
                .split(multipleSplitter)
                .map((value) => value.trim())
                .filter((value) => validateCreatedNewValue(value));
        }, [inputValue]);
        const hasSearchedValue = React.useCallback(
            (target: IMultiSelectItem) => {
                const values = canCreateNew ? parsedMultipleValues : [inputValue];
                return values.some((value) => target.value.includes(value) || target.label.includes(value));
            },
            [inputValue]
        );

        React.useEffect(() => {
            if (selectedValue) {
                setSelected(selectedValue);
            }
        }, [selectedValue]);

        React.useEffect(() => {
            setSelected(selectedValue ?? []);
        }, [selectedValue]);

        const boundary: (Element | null)[] = [];
        const hasExplicitBoundaries = boundary.length > 0;

        const detectOverflowOptions = {
            padding: 0,
            boundary: boundary.filter((value: Element | null) => value !== null),
            altBoundary: hasExplicitBoundaries,
        };

        const { refs, floatingStyles, isPositioned, middlewareData } = useFloating({
            strategy: "absolute",
            placement: "bottom",
            whileElementsMounted: (...args) => {
                const cleanup = autoUpdate(...args, {
                    animationFrame: false,
                });
                return cleanup;
            },
            elements: {
                reference: wrapper,
            },
            middleware: [
                offset({ mainAxis: 0, alignmentAxis: 0 }),
                shift({
                    mainAxis: true,
                    crossAxis: false,
                    limiter: limitShift(),
                    ...detectOverflowOptions,
                }),
                flip({ ...detectOverflowOptions }),
                size({
                    ...detectOverflowOptions,
                    apply: ({ elements, availableWidth, availableHeight }) => {
                        const contentStyle = elements.floating.style;
                        contentStyle.setProperty("--multiselect-available-width", `${availableWidth}px`);
                        contentStyle.setProperty("--multiselect-available-height", `${availableHeight}px`);
                    },
                }),
                hide({ strategy: "referenceHidden", ...detectOverflowOptions }),
            ],
        });

        const style = {
            ...floatingStyles,
            transform: isPositioned ? floatingStyles.transform : "translate(0, -200%)",
            "--multiselect-transform-origin": [middlewareData.transformOrigin?.x, middlewareData.transformOrigin?.y].join(" "),
        };

        const isDisabled = !noBadge && ((!canCreateNew && !selectables.length) || !!disabled);
        const isCreatable =
            !!canCreateNew &&
            !!validateCreatedNewValue &&
            !!inputValue &&
            inputValue.split(multipleSplitter).some((value) => validateCreatedNewValue(value.trim()));

        return (
            <Command.Root onKeyDown={handleKeyDown} className={cn("overflow-visible bg-transparent", className)}>
                <Box
                    px="3"
                    py="2"
                    textSize="sm"
                    border
                    rounded="md"
                    className={cn(
                        "group w-full border-input ring-offset-background focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0",
                        noBadge && "cursor-pointer"
                    )}
                    onClick={() => {
                        if (noBadge) {
                            setOpen((prev) => !prev);
                        }
                    }}
                    ref={setWrapper}
                >
                    <Flex wrap gap="1" className={badgeListClassName} ref={badgeListRef}>
                        {noBadge ? (
                            <Flex items="center" gap="2" w="full" justify="between">
                                {selected.length ? createValueText?.(selected) : placeholder}
                                <IconComponent
                                    icon="chevron-down"
                                    size="4"
                                    className={cn(
                                        "text-muted-foreground opacity-75 transition-all",
                                        open ? "rotate-180" : "rotate-0",
                                        isDisabled ? "cursor-not-allowed" : "hover:text-foreground"
                                    )}
                                />
                            </Flex>
                        ) : (
                            <>
                                {selected.map((selectedValue) => {
                                    let selection = selections.find((selection) => selection.value === selectedValue);
                                    if (!selection && canCreateNew) {
                                        selection = { label: selectedValue, value: selectedValue };
                                    }

                                    if (!selection) {
                                        return null;
                                    }

                                    let badge: React.JSX.Element | null = (
                                        <Badge variant="secondary" className={cn("justify-between gap-1 pl-2 pr-1", badgeClassName)}>
                                            {selection!.label}
                                            <button
                                                className="ml-1 rounded-full outline-none ring-offset-background focus:ring-2 focus:ring-ring focus:ring-offset-2"
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter") {
                                                        handleUnselect(selectedValue);
                                                    }
                                                }}
                                                onMouseDown={(e) => {
                                                    e.preventDefault();
                                                    e.stopPropagation();
                                                }}
                                                disabled={disabled}
                                                onClick={() => handleUnselect(selectedValue)}
                                            >
                                                <IconComponent
                                                    icon="x"
                                                    size="3"
                                                    className={cn(
                                                        "text-muted-foreground transition-all",
                                                        disabled ? "cursor-not-allowed" : "hover:text-foreground"
                                                    )}
                                                />
                                            </button>
                                        </Badge>
                                    );

                                    if (createBadgeWrapper) {
                                        badge = createBadgeWrapper(badge, selectedValue);
                                        if (!badge) {
                                            return null;
                                        }
                                    }

                                    return (
                                        <span key={selection!.value} className="inline-flex">
                                            {badge}
                                        </span>
                                    );
                                })}
                            </>
                        )}
                        <CommandPrimitive.Input
                            ref={inputRef}
                            value={inputValue}
                            onValueChange={handleChangeInputValue}
                            onBlur={() => setOpen(false)}
                            onFocus={() => setOpen(true)}
                            placeholder={placeholder}
                            disabled={isDisabled}
                            className={cn(
                                "ml-2 flex-1 bg-transparent outline-none placeholder:text-muted-foreground",
                                inputClassName,
                                noBadge && "hidden"
                            )}
                        />
                    </Flex>
                </Box>
                <Box position="relative" mt="2" className={listClassName}>
                    <Command.List
                        style={style}
                        className={cn(
                            "absolute z-50 w-full min-w-max bg-popover text-popover-foreground shadow-md transition-[height]",
                            "max-h-[calc(min(var(--multiselect-available-height)_-_theme(spacing.4),350px))]",
                            "[&>[cmdk-list-sizer]]:w-full",
                            !open || isDisabled ? "hidden" : "rounded-md border",
                            middlewareData.hide?.referenceHidden && "pointer-events-none invisible",
                            open && selectables.length === 0 && !isCreatable && "border-none"
                        )}
                        ref={refs.setFloating}
                    >
                        {open && ((!noBadge && selectables.length > 0) || (noBadge && selections.length) || isCreatable) ? (
                            <MultiSelectItemList
                                isCreatable={isCreatable}
                                selectables={noBadge ? selections : selectables}
                                selected={selected}
                                inputValue={inputValue}
                                setInputValue={setInputValue}
                                hasSearchedValue={hasSearchedValue}
                                createNewCommandItemLabel={createNewCommandItemLabel as never}
                                parsedMultipleValues={parsedMultipleValues}
                                isNewCommandItemMultiple={isNewCommandItemMultiple as never}
                                renderSelectableItem={renderSelectableItem}
                                setSelectedWithFireEvent={setSelectedWithFireEvent}
                                disabled={isDisabled}
                                noBagde={noBadge}
                            />
                        ) : null}
                    </Command.List>
                </Box>
            </Command.Root>
        );
    }
);
MultiSelect.displayName = "MultiSelect";

interface IBaseMultiSelectItemListProps {
    isCreatable: bool;
    selectables: IMultiSelectItem[];
    selected: string[];
    inputValue: string;
    setInputValue: React.Dispatch<React.SetStateAction<string>>;
    createNewCommandItemLabel?: ((values: string[]) => string) | ((value: string) => { label: string; value: string }[]);
    hasSearchedValue: (target: IMultiSelectItem) => bool;
    parsedMultipleValues: string[];
    isNewCommandItemMultiple?: bool;
    renderSelectableItem?: (item: IMultiSelectItem) => React.ReactNode;
    setSelectedWithFireEvent: (action: React.SetStateAction<string[]>) => void;
    noBagde?: bool;
    disabled?: bool;
}

interface IMultipleCommandItemMultiSelectItemListProps extends IBaseMultiSelectItemListProps {
    isNewCommandItemMultiple: true;
    createNewCommandItemLabel: (value: string) => { label: string; value: string }[];
}

interface IDefaultMultiSelectItemListProps extends IBaseMultiSelectItemListProps {
    isNewCommandItemMultiple?: false;
    createNewCommandItemLabel?: (values: string[]) => string;
}

export type TMultiSelectItemListProps = IMultipleCommandItemMultiSelectItemListProps | IDefaultMultiSelectItemListProps;

const MultiSelectItemList = React.memo(
    ({
        isCreatable,
        selectables,
        selected,
        inputValue,
        setInputValue,
        hasSearchedValue,
        createNewCommandItemLabel,
        parsedMultipleValues,
        isNewCommandItemMultiple,
        renderSelectableItem,
        setSelectedWithFireEvent,
        noBagde,
        disabled,
    }: TMultiSelectItemListProps) => {
        const newItems = React.useMemo(() => {
            if (!isCreatable) {
                return [];
            }

            if (!isNewCommandItemMultiple) {
                return [
                    {
                        label: createNewCommandItemLabel ? createNewCommandItemLabel(parsedMultipleValues) : parsedMultipleValues.join(", "),
                        value: parsedMultipleValues,
                    },
                ];
            }

            return parsedMultipleValues
                .map((value) =>
                    createNewCommandItemLabel
                        ? createNewCommandItemLabel(value)
                        : [
                              {
                                  label: value,
                                  value,
                              },
                          ]
                )
                .flat();
        }, [parsedMultipleValues]);

        const onPointerDown = (e: React.MouseEvent | React.TouchEvent) => {
            e.preventDefault();
            e.stopPropagation();
        };

        const onSelect = React.useCallback(
            (values: string[]) => {
                setInputValue("");
                setSelectedWithFireEvent((prev) => {
                    if (noBagde) {
                        const newValues = prev.filter((value) => !values.includes(value));
                        for (let i = 0; i < values.length; ++i) {
                            const value = values[i];
                            if (!prev.includes(value)) {
                                newValues.push(value);
                            }
                        }
                        return newValues;
                    } else {
                        return [...prev, ...values.filter((value) => !selected.includes(value))];
                    }
                });
            },
            [setSelectedWithFireEvent]
        );

        return (
            <Box w="full" className="outline-none animate-in">
                <Command.Group className="h-full overflow-auto" forceMount onMouseDown={onPointerDown} onTouchStart={onPointerDown}>
                    {newItems.map((newItem) => (
                        <Command.Item
                            key={Utils.String.Token.shortUUID()}
                            onSelect={() => onSelect(Utils.Type.isArray(newItem.value) ? newItem.value : [newItem.value])}
                            disabled={disabled}
                            className="cursor-pointer"
                        >
                            {newItem.label}
                        </Command.Item>
                    ))}
                    {selectables.map((selectable) => {
                        if (noBagde) {
                            return (
                                <Command.Item
                                    key={selectable.value}
                                    onSelect={() => onSelect([selectable.value])}
                                    disabled={disabled}
                                    className="flex cursor-pointer items-center gap-2"
                                >
                                    {selected.includes(selectable.value) ? <IconComponent icon="check" size="4" /> : <Box size="4" />}
                                    {renderSelectableItem ? renderSelectableItem(selectable) : selectable.label}
                                </Command.Item>
                            );
                        }

                        if (inputValue.length > 0) {
                            if (!hasSearchedValue(selectable)) {
                                return null;
                            }
                        }

                        return (
                            <Command.Item
                                key={selectable.value}
                                onSelect={() => onSelect([selectable.value])}
                                disabled={disabled}
                                className="cursor-pointer"
                            >
                                {renderSelectableItem ? renderSelectableItem(selectable) : selectable.label}
                            </Command.Item>
                        );
                    })}
                </Command.Group>
            </Box>
        );
    }
);

export default MultiSelect;
