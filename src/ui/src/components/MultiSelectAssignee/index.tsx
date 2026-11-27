/* eslint-disable @typescript-eslint/no-explicit-any */
import * as PopoverPrimitive from "@radix-ui/react-popover";
import Button, { type ButtonProps } from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import BasePopover from "@/components/base/Popover";
import SubmitButton from "@/components/base/SubmitButton";
import DropdownMenu from "@/components/base/DropdownMenu";
import {
    ISelectEditorProviderProps,
    SelectEditor,
    SelectEditorCombobox,
    SelectEditorContent,
    SelectEditorInput,
    TSelectItem,
} from "@/components/Editor/select-editor";
import UserAvatar from "@/components/UserAvatar";
import UserAvatarDefaultList from "@/components/UserAvatarDefaultList";
import { BotModel, User, UserGroup } from "@/core/models";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { IUserAvatarListProps, UserAvatarList } from "@/components/UserAvatarList";
import { TIconProps } from "@/components/base/IconComponent";
import { Utils } from "@langboard/core/utils";
import { ModelRegistry, TUserLikeModelName, TUserLikeModel } from "@/core/models/ModelRegistry";
import UserLikeComponent from "@/components/UserLikeComponent";

type TAssigneeValue = string | TUserLikeModel;

type TBaseAssigneeSelectItem = {
    assigneeModelName: TUserLikeModelName;
    assigneeUID: string;
};

export type TAssigneeSelecItem = TSelectItem & TBaseAssigneeSelectItem & Record<string, unknown>;

export type TSaveHandler =
    | ((assignees: TUserLikeModel[]) => void)
    | ((assignees: TUserLikeModel[]) => Promise<void>)
    | ((assignees: (string | TUserLikeModel)[]) => void)
    | ((assignees: (string | TUserLikeModel)[]) => Promise<void>);

const createAssigneeSelectItemCreator =
    (
        createSearchKeywords: (item: TAssigneeValue) => string[],
        createLabel: (item: TAssigneeValue) => string,
        decorateSelectItem?: (item: TAssigneeValue) => Record<string, unknown>
    ) =>
    (item: TAssigneeValue): TAssigneeSelecItem =>
        Utils.Type.isString(item)
            ? {
                  value: item,
                  label: createLabel(item),
                  keywords: createSearchKeywords(item),
                  assigneeModelName: "" as TUserLikeModelName,
                  assigneeUID: "",
                  isNew: true,
                  ...decorateSelectItem?.(item),
              }
            : {
                  value: item.uid,
                  label: createLabel(item),
                  keywords: createSearchKeywords(item),
                  assigneeModelName: item.MODEL_NAME as TUserLikeModelName,
                  assigneeUID: item.uid,
                  ...decorateSelectItem?.(item),
              };

const hasSameAssigneeUIDs = (a: (string | TUserLikeModel)[], b: (string | TUserLikeModel)[]) => {
    if (a.length !== b.length) {
        return false;
    }

    for (let i = 0; i < a.length; ++i) {
        const left = a[i];
        const right = b[i];
        const leftUID = Utils.Type.isString(left) ? left : left?.uid;
        const rightUID = Utils.Type.isString(right) ? right : right?.uid;
        if (leftUID !== rightUID) {
            return false;
        }
    }

    return true;
};

const resolveSelectableByUID = (items: TUserLikeModel[], uid: string) => {
    return items.find((item) => item.uid === uid);
};

export interface IPopoverProps
    extends
        Omit<IFormProps, "useEditorProps">,
        Pick<
            Required<IFormProps>["useEditorProps"],
            "save" | "canAddNew" | "validateNewItem" | "createNewItemLabel" | "withUserGroups" | "groups" | "filterGroupUser"
        > {
    selectedAssignees?: TAssigneeValue[];
    onOpenChange?: (open: bool) => void;
    onSelectedAssigneesChange?: (items: TAssigneeValue[]) => void;
    popoverButtonProps?: ButtonProps;
    popoverContentProps?: React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>;
    userAvatarListProps?: Omit<IUserAvatarListProps, "userOrBots">;
    showableAssignees?: TUserLikeModel[];
    multiSelectProps?: Omit<React.ComponentPropsWithoutRef<typeof SelectEditor>, "value" | "onValueChange" | "items">;
    addIcon?: React.ComponentPropsWithoutRef<TIconProps>["icon"];
    addIconSize?: React.ComponentPropsWithoutRef<TIconProps>["size"];
    canEdit?: bool;
    saveOnChange?: bool;
    saveText?: string;
    helperContent?: React.ReactNode;
}

const Popover = memo((props: IPopoverProps) => {
    const { userAvatarListProps, originalAssignees, showableAssignees = originalAssignees, tagContentProps = {}, canEdit } = props;

    return (
        <Flex items="center" gap="1">
            {showableAssignees.length > 0 && <UserAvatarList userOrBots={showableAssignees} {...(tagContentProps as any)} {...userAvatarListProps} />}
            {canEdit && <PopoverInner {...props} />}
        </Flex>
    );
});

const PopoverInner = memo((props: IPopoverProps) => {
    const [t] = useTranslation();
    const {
        popoverButtonProps = {},
        popoverContentProps,
        addIcon = "plus",
        addIconSize,
        canAddNew = false,
        save,
        saveText,
        withUserGroups,
        groups,
        filterGroupUser,
        validateNewItem,
        createNewItemLabel,
        allSelectables,
        selectedAssignees,
        onOpenChange,
        onSelectedAssigneesChange,
        saveOnChange = false,
        helperContent,
    } = props;
    const { variant: popoverButtonVariant = "outline" } = popoverButtonProps;
    const [isValidating, setIsValidating] = useState(false);
    const [isOpened, setIsOpened] = useState(false);
    const [selectedValues, setSelectedValues] = useState<(string | TUserLikeModel)[]>(selectedAssignees ?? props.originalAssignees);
    const isSyncingFromPropsRef = useRef(false);
    const isSavingRef = useRef(false);
    const pendingSaveValuesRef = useRef<{ values: (string | TUserLikeModel)[]; closeOnSuccess: bool } | null>(null);

    useEffect(() => {
        const nextSelectedValues = selectedAssignees ?? props.originalAssignees;
        isSyncingFromPropsRef.current = true;
        setSelectedValues((prev) => (hasSameAssigneeUIDs(prev, nextSelectedValues) ? prev : nextSelectedValues));
        const timeout = window.setTimeout(() => {
            isSyncingFromPropsRef.current = false;
        }, 0);

        return () => {
            clearTimeout(timeout);
        };
    }, [props.originalAssignees, selectedAssignees]);

    const handleOpenChange = useCallback(
        (open: bool) => {
            setIsOpened(open);
            onOpenChange?.(open);
        },
        [onOpenChange]
    );

    const getAssigneeValues = useCallback(
        (items: (string | TUserLikeModel)[]) =>
            items
                .map((item) => {
                    if (!item) {
                        return undefined;
                    }

                    if (Utils.Type.isString(item)) {
                        return item;
                    }

                    if (Utils.Type.isString(item.uid)) {
                        return item;
                    }

                    const itemRecord = item as unknown as Record<string, unknown>;
                    const assigneeUID = Utils.Type.isString(itemRecord.assigneeUID) ? itemRecord.assigneeUID : undefined;
                    const valueUID = Utils.Type.isString(itemRecord.value) ? itemRecord.value : undefined;
                    const resolvedSelectable = resolveSelectableByUID(allSelectables, assigneeUID ?? valueUID ?? "");
                    if (resolvedSelectable) {
                        return resolvedSelectable;
                    }

                    if (itemRecord.isNew && Utils.Type.isString(itemRecord.value)) {
                        return itemRecord.value;
                    }

                    return undefined;
                })
                .filter((item): item is TAssigneeValue => !!item),
        [allSelectables]
    );

    const saveSelectedValues = useCallback(
        async (values: (string | TUserLikeModel)[], closeOnSuccess: bool) => {
            if (isSavingRef.current) {
                pendingSaveValuesRef.current = { values, closeOnSuccess };
                return;
            }

            isSavingRef.current = true;
            setIsValidating(true);

            try {
                let saveValues = values;
                let shouldClose = closeOnSuccess;

                while (true) {
                    await save(saveValues as any);

                    const pendingSaveValues = pendingSaveValuesRef.current;
                    pendingSaveValuesRef.current = null;
                    if (!pendingSaveValues || hasSameAssigneeUIDs(saveValues, pendingSaveValues.values)) {
                        if (shouldClose) {
                            setIsOpened(false);
                        }
                        return;
                    }

                    saveValues = pendingSaveValues.values;
                    shouldClose = shouldClose || pendingSaveValues.closeOnSuccess;
                }
            } catch (error) {
                pendingSaveValuesRef.current = null;
                throw error;
            } finally {
                isSavingRef.current = false;
                setIsValidating(false);
            }
        },
        [save]
    );

    const handleSelectedValuesChange = useCallback(
        (items: (string | TUserLikeModel)[]) => {
            const previousSelectedValues = selectedValues;
            const nextAssigneeValues = getAssigneeValues(items);
            const previousAssigneeValues = getAssigneeValues(previousSelectedValues);

            setSelectedValues(items);
            if (isSyncingFromPropsRef.current) {
                return;
            }

            if (hasSameAssigneeUIDs(previousAssigneeValues, nextAssigneeValues)) {
                return;
            }

            onSelectedAssigneesChange?.(nextAssigneeValues);

            if (saveOnChange) {
                void saveSelectedValues(nextAssigneeValues, false).catch(() => {
                    setSelectedValues(previousSelectedValues);
                    onSelectedAssigneesChange?.(getAssigneeValues(previousSelectedValues));
                });
            }
        },
        [getAssigneeValues, onSelectedAssigneesChange, saveOnChange, saveSelectedValues, selectedValues]
    );

    const handleSave = useCallback(async () => {
        if (isValidating) {
            return;
        }

        await saveSelectedValues(getAssigneeValues(selectedValues), true);
    }, [getAssigneeValues, isValidating, saveSelectedValues, selectedValues]);

    return (
        <BasePopover.Root modal open={isOpened} onOpenChange={handleOpenChange}>
            <BasePopover.Trigger asChild>
                <Button variant={popoverButtonVariant} {...popoverButtonProps}>
                    <IconComponent icon={addIcon} size={addIconSize} />
                </Button>
            </BasePopover.Trigger>
            <BasePopover.Content {...popoverContentProps}>
                <Form
                    {...props}
                    originalAssignees={selectedValues}
                    useEditorProps={{
                        useButton: false,
                        isValidating,
                        readOnly: false,
                        setReadOnly: () => {},
                        canAddNew,
                        onValueChange: handleSelectedValuesChange,
                        save: handleSave,
                        withUserGroups: withUserGroups as true,
                        groups: groups as UserGroup.TModel[],
                        filterGroupUser,
                        validateNewItem,
                        createNewItemLabel,
                    }}
                />
                {helperContent}
                {!saveOnChange && (
                    <Flex items="center" justify="end" gap="1" mt="2">
                        <Button type="button" variant="secondary" size="sm" disabled={isValidating} onClick={() => setIsOpened(false)}>
                            {t("common.Cancel")}
                        </Button>
                        <SubmitButton type="button" size="sm" onClick={handleSave} isValidating={isValidating}>
                            {saveText ?? t("common.Save")}
                        </SubmitButton>
                    </Flex>
                )}
            </BasePopover.Content>
        </BasePopover.Root>
    );
});

export interface IFormProps {
    TagContent?: React.ComponentType<TAssigneeSelecItem & TBaseAssigneeSelectItem & { label?: string; readOnly: bool } & Record<string, unknown>>;
    tagContentProps?: Record<string, unknown>;
    renderSelectableItem?: (item: TUserLikeModel) => React.ReactNode;
    decorateSelectItem?: (item: TAssigneeValue) => Record<string, unknown>;
    allSelectables: TUserLikeModel[];
    originalAssignees: TAssigneeValue[];
    createSearchKeywords: (item: TAssigneeValue) => string[];
    createLabel: (item: TAssigneeValue) => string;
    onSearchChange?: (search: string) => void;
    placeholder?: string;
    useEditorProps?: {
        canAddNew?: bool;
        useButton?: bool;
        isValidating: bool;
        readOnly: bool;
        setReadOnly: (readOnly: bool) => void;
        onValueChange?: (items: TAssigneeValue[]) => void;
        save: TSaveHandler;
        withUserGroups?: bool;
        groups?: UserGroup.TModel[];
        filterGroupUser?: (user: User.TModel) => bool;
    } & Pick<ISelectEditorProviderProps, "validateNewItem" | "createNewItemLabel">;
}

const Form = memo(
    ({
        TagContent = FormTagContent,
        tagContentProps = {},
        allSelectables,
        originalAssignees,
        renderSelectableItem,
        decorateSelectItem,
        createSearchKeywords,
        createLabel,
        onSearchChange,
        placeholder,
        useEditorProps,
    }: IFormProps) => {
        const [t] = useTranslation();
        const createAssigneeSelectItem = useMemo(
            () => createAssigneeSelectItemCreator(createSearchKeywords, createLabel, decorateSelectItem),
            [createLabel, createSearchKeywords, decorateSelectItem]
        );
        const [selectables, selectablesMap] = useMemo(() => {
            const list: TAssigneeSelecItem[] = [];
            const map: Record<string, TUserLikeModel> = {};
            for (let i = 0; i < allSelectables.length; ++i) {
                const selectable = allSelectables[i];
                list.push(createAssigneeSelectItem(selectable));
                map[`${selectable.MODEL_NAME}_${selectable.uid}`] = selectable;
            }
            return [list, map];
        }, [allSelectables, createAssigneeSelectItem]);
        const [selectedValues, setSelectedValues] = useState<TAssigneeSelecItem[]>(originalAssignees.map(createAssigneeSelectItem));
        const isSyncingFromOriginalAssigneesRef = useRef(false);
        const getSelectable = useCallback(
            (item: TAssigneeSelecItem) => {
                return selectablesMap[`${item.assigneeModelName}_${item.assigneeUID}`];
            },
            [selectablesMap]
        );
        const getSelectableByValue = useCallback(
            (item: TSelectItem) => {
                const selectable = getSelectable(item as TAssigneeSelecItem);
                if (selectable) {
                    return selectable;
                }

                return allSelectables.find((selectableItem) => selectableItem.uid === item.value);
            },
            [allSelectables, getSelectable]
        );

        useEffect(() => {
            const newSelectedAssignees = originalAssignees.map(createAssigneeSelectItem);
            isSyncingFromOriginalAssigneesRef.current = true;

            setSelectedValues((prev) => {
                if (
                    prev.length === newSelectedAssignees.length &&
                    prev.every((item, index) => {
                        const nextItem = newSelectedAssignees[index];
                        return (
                            item.assigneeUID === nextItem?.assigneeUID &&
                            item.value === nextItem?.value &&
                            ("badgeBorderColor" in item ? item.badgeBorderColor : undefined) ===
                                ("badgeBorderColor" in (nextItem ?? {}) ? nextItem.badgeBorderColor : undefined) &&
                            ("badgeActorName" in item ? item.badgeActorName : undefined) ===
                                ("badgeActorName" in (nextItem ?? {}) ? nextItem.badgeActorName : undefined)
                        );
                    })
                ) {
                    return prev;
                }

                return newSelectedAssignees;
            });

            const timeout = window.setTimeout(() => {
                isSyncingFromOriginalAssigneesRef.current = false;
            }, 0);

            return () => {
                clearTimeout(timeout);
            };
        }, [createAssigneeSelectItem, originalAssignees]);

        const handleValueChange = useCallback(
            (items: TSelectItem[]) => {
                if (isSyncingFromOriginalAssigneesRef.current) {
                    setSelectedValues(items as TAssigneeSelecItem[]);
                    return;
                }

                if (useEditorProps) {
                    setSelectedValues(items as TAssigneeSelecItem[]);
                    useEditorProps.onValueChange?.(
                        items.map((item) => (item.isNew ? item.value : getSelectableByValue(item))).filter((item) => !!item) as any
                    );
                }
            },
            [getSelectableByValue, useEditorProps?.onValueChange]
        );

        const handleSave = useCallback(async () => {
            if (!useEditorProps) {
                return;
            }

            if (useEditorProps.readOnly) {
                useEditorProps.setReadOnly(false);
                return;
            }

            await useEditorProps.save(selectedValues.map((item) => (item.isNew ? item.value : getSelectable(item))).filter((item) => !!item) as any);
            useEditorProps.setReadOnly(true);
        }, [selectedValues, useEditorProps?.save]);

        return (
            <Flex direction="col" py="4" gap="2">
                <Flex items="center" gap="1">
                    <SelectEditor
                        value={selectedValues}
                        onValueChange={handleValueChange}
                        items={selectables}
                        renderItem={(item) => {
                            const selectable = getSelectableByValue(item);
                            if (!selectable) {
                                return item.label ?? item.value;
                            }

                            return renderSelectableItem ? renderSelectableItem(selectable) : (item.label ?? item.value);
                        }}
                        createTagContent={
                            ((props: TAssigneeSelecItem & { readOnly: bool; label?: string }) => <TagContent {...props} {...tagContentProps} />) as (
                                props: TSelectItem & { readOnly: bool }
                            ) => React.JSX.Element
                        }
                        canAddNew={useEditorProps?.canAddNew}
                        validateNewItem={useEditorProps?.validateNewItem}
                        createNewItemLabel={useEditorProps?.createNewItemLabel}
                        onSearchChange={onSearchChange}
                    >
                        <SelectEditorContent>
                            <SelectEditorInput
                                readOnly={useEditorProps?.readOnly}
                                disabled={useEditorProps?.isValidating}
                                placeholder={placeholder}
                            />
                            <SelectEditorCombobox />
                        </SelectEditorContent>
                    </SelectEditor>
                    {useEditorProps && useEditorProps.useButton && (
                        <Button
                            size="icon"
                            variant="ghost"
                            className="size-10"
                            title={useEditorProps.readOnly ? t("common.Edit") : t("common.Save")}
                            titleSide="bottom"
                            onClick={handleSave}
                            disabled={useEditorProps.isValidating}
                            type="button"
                        >
                            <IconComponent icon={useEditorProps.readOnly ? "plus" : "check"} size="4" />
                        </Button>
                    )}
                </Flex>
                {useEditorProps && useEditorProps.withUserGroups && !useEditorProps.readOnly && useEditorProps.groups && (
                    <UserGroupSelectDropdownMenu
                        isValidating={useEditorProps.isValidating}
                        groups={useEditorProps.groups}
                        filterGroupUser={useEditorProps.filterGroupUser}
                        selectedValues={selectedValues}
                        onValueChange={handleValueChange}
                        createAssigneeSelectItem={createAssigneeSelectItem}
                    />
                )}
            </Flex>
        );
    }
);

function FormTagContent({
    assigneeModelName,
    assigneeUID,
    ...props
}: TAssigneeSelecItem & { readOnly: bool; label?: string } & Record<string, unknown>) {
    if (!assigneeModelName || !assigneeUID) {
        return props.value;
    }

    const model = ModelRegistry[assigneeModelName];
    if (!model) {
        return props.value;
    }

    const assignee = model.Model.getModel(assigneeUID);
    if (!assignee) {
        return props.value;
    }

    if (props.isNew) {
        return props.value;
    }

    return <UserLikeComponent userOrBot={assignee} userComp={MultiSelectUserTagContent} botComp={MultiSelectBotTagContent} props={{ ...props }} />;
}

function MultiSelectBotTagContent({
    bot,
    label,
    readOnly,
    ...props
}: Omit<TAssigneeSelecItem, keyof TBaseAssigneeSelectItem> & { bot: BotModel.TModel; label?: string; readOnly: bool } & Record<string, unknown>) {
    const name = bot.useField("name");
    const botUname = bot.useField("bot_uname");

    return (
        <UserAvatar.Root userOrBot={bot} customTrigger={<>{label ?? `${name} (${botUname})`}</>}>
            <UserAvatarDefaultList userOrBot={bot} {...props} />
        </UserAvatar.Root>
    );
}

function MultiSelectUserTagContent({
    user,
    label,
    readOnly,
    ...props
}: Omit<TAssigneeSelecItem, keyof TBaseAssigneeSelectItem> & { user: User.TModel; label?: string; readOnly: bool } & Record<string, unknown>) {
    const firstname = user.useField("firstname");
    const lastname = user.useField("lastname");

    return (
        <UserAvatar.Root userOrBot={user} customTrigger={label ?? `${firstname} ${lastname}`}>
            <UserAvatarDefaultList userOrBot={user} {...props} />
        </UserAvatar.Root>
    );
}

type TUserGroupSelectDropdownMenuProps = Pick<Required<IFormProps>["useEditorProps"], "isValidating" | "filterGroupUser"> & {
    groups: UserGroup.TModel[];
    selectedValues: TAssigneeSelecItem[];
    onValueChange: (items: TSelectItem[]) => void;
    createAssigneeSelectItem: (item: TUserLikeModel) => TAssigneeSelecItem;
};

function UserGroupSelectDropdownMenu({
    isValidating,
    groups,
    selectedValues,
    filterGroupUser,
    onValueChange,
    createAssigneeSelectItem,
}: TUserGroupSelectDropdownMenuProps) {
    const [t] = useTranslation();
    const [isOpened, setIsOpened] = useState(false);
    const selectableGroups = useMemo(() => {
        const newUsers: Record<string, { uid: string; name: string; users: User.TModel[] }> = {};
        for (let i = 0; i < groups.length; ++i) {
            const group = groups[i];
            const users = [...group.users].filter(
                (user) => (filterGroupUser?.(user) ?? true) && !selectedValues.some((item) => item.assigneeUID === user.uid)
            );

            if (users.length > 0) {
                newUsers[group.uid] = {
                    uid: group.uid,
                    name: group.name,
                    users,
                };
            }
        }
        return newUsers;
    }, [selectedValues, groups]);

    const addGroupMembers = (e: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
        const groupUID = e.currentTarget.dataset.uid;
        if (!groupUID || !selectableGroups[groupUID]) {
            return;
        }

        const group = selectableGroups[groupUID];
        const newItems = group.users.map(createAssigneeSelectItem);

        onValueChange([...selectedValues, ...newItems]);
    };

    return (
        <DropdownMenu.Root open={isOpened} onOpenChange={setIsOpened}>
            <DropdownMenu.Trigger asChild>
                <Button variant="secondary" size="sm" disabled={!Object.keys(selectableGroups).length || isValidating}>
                    {t("common.Add members from group")}
                </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content>
                <DropdownMenu.Group>
                    {Object.values(selectableGroups).map((group) => (
                        <DropdownMenu.Item key={`group-${group.uid}`} data-uid={group.uid} onClick={addGroupMembers}>
                            {group.name}
                        </DropdownMenu.Item>
                    ))}
                </DropdownMenu.Group>
            </DropdownMenu.Content>
        </DropdownMenu.Root>
    );
}

export default { Form, Popover };
