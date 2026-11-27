import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import Input from "@/components/base/Input";
import SubmitButton from "@/components/base/SubmitButton";
import Textarea from "@/components/base/Textarea";
import Collaborative, { useCollaborativeText } from "@/components/Collaborative";
import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";
import MultiSelect, { type IMultiSelectItem } from "@/components/MultiSelect";
import Tooltip from "@/components/base/Tooltip";
import { API_COMFORT_TOOL_COLLABORATION_SECTION } from "@/pages/SettingsPage/components/apiComfortTools/types";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

interface IApiSelectionMeta {
    added: bool;
    apiName: string;
    updatedAt: number;
}

interface IRemoteApiMetaState {
    actorName: string;
    added: bool;
    borderColor: string;
    updatedAt: number;
}

interface IApiComfortToolDialogProps {
    apiNames: string[];
    apiSelections: IMultiSelectItem[];
    description: string;
    disabled: bool;
    editingName?: string;
    errors: Record<string, string>;
    label: string;
    name: string;
    open: bool;
    onApiNamesChange: (apiNames: string[]) => void;
    onApiNamesRemoteChange: (apiNames: string[]) => void;
    onApiNamesResetReady: (resetValue: ((value: string) => void) | null) => void;
    onClose: () => void;
    onDescriptionChange: (description: string) => void;
    onDescriptionResetReady: (resetValue: ((value: string) => void) | null) => void;
    onLabelChange: (label: string) => void;
    onLabelResetReady: (resetValue: ((value: string) => void) | null) => void;
    onNameChange: (name: string) => void;
    onNameResetReady: (resetValue: ((value: string) => void) | null) => void;
    onOpenChange: (open: bool) => void;
    onSave: () => void;
}

function ApiComfortToolDialog({
    apiNames,
    apiSelections,
    description,
    disabled,
    editingName,
    errors,
    label,
    name,
    open,
    onApiNamesChange,
    onApiNamesRemoteChange,
    onApiNamesResetReady,
    onClose,
    onDescriptionChange,
    onDescriptionResetReady,
    onLabelChange,
    onLabelResetReady,
    onNameChange,
    onNameResetReady,
    onOpenChange,
    onSave,
}: IApiComfortToolDialogProps) {
    const [t] = useTranslation();
    const labelInputRef = useRef<HTMLInputElement | null>(null);

    return (
        <Dialog.Root open={open} onOpenChange={onOpenChange}>
            <Dialog.Content
                className="sm:max-w-2xl"
                aria-describedby=""
                onOpenAutoFocus={(event) => {
                    event.preventDefault();
                    labelInputRef.current?.focus();
                }}
                onEscapeKeyDown={(event) => {
                    const activeElement = document.activeElement;
                    if (activeElement instanceof HTMLElement && activeElement.closest(".api-comfort-tool-api-select")) {
                        event.preventDefault();
                        event.stopPropagation();
                    }
                }}
            >
                <Dialog.Header>
                    <Dialog.Title>{editingName ? t("settings.Edit API comfort tool") : t("settings.Create API comfort tool")}</Dialog.Title>
                </Dialog.Header>
                <Flex direction="col" gap="3" mt="4">
                    <Flex gap="3" className="max-sm:flex-col">
                        {editingName ? (
                            <>
                                <Collaborative.Input
                                    key={`${editingName}-label`}
                                    ref={labelInputRef}
                                    collaborationType={EEditorCollaborationType.AppSettings}
                                    uid={editingName}
                                    section={API_COMFORT_TOOL_COLLABORATION_SECTION}
                                    field="label"
                                    defaultValue={label}
                                    placeholder={t("settings.Comfort tool label")}
                                    disabled={disabled}
                                    onCollaborativeValueResetReady={onLabelResetReady}
                                    onValueChange={onLabelChange}
                                />
                                <Collaborative.Input
                                    key={`${editingName}-name`}
                                    collaborationType={EEditorCollaborationType.AppSettings}
                                    uid={editingName}
                                    section={API_COMFORT_TOOL_COLLABORATION_SECTION}
                                    field="name"
                                    defaultValue={name}
                                    placeholder={t("settings.Comfort tool name")}
                                    disabled={disabled}
                                    onCollaborativeValueResetReady={onNameResetReady}
                                    onValueChange={onNameChange}
                                />
                            </>
                        ) : (
                            <>
                                <Input
                                    ref={labelInputRef}
                                    value={label}
                                    placeholder={t("settings.Comfort tool label")}
                                    disabled={disabled}
                                    onChange={(event) => onLabelChange(event.target.value)}
                                />
                                <Input
                                    value={name}
                                    placeholder={t("settings.Comfort tool name")}
                                    disabled={disabled}
                                    onChange={(event) => onNameChange(event.target.value)}
                                />
                            </>
                        )}
                    </Flex>
                    {errors.label ? (
                        <Box textSize="sm" className="text-destructive">
                            {errors.label}
                        </Box>
                    ) : null}
                    {errors.name ? (
                        <Box textSize="sm" className="text-destructive">
                            {errors.name}
                        </Box>
                    ) : null}
                    {editingName ? (
                        <Collaborative.Textarea
                            key={`${editingName}-description`}
                            collaborationType={EEditorCollaborationType.AppSettings}
                            uid={editingName}
                            section={API_COMFORT_TOOL_COLLABORATION_SECTION}
                            field="description"
                            defaultValue={description}
                            placeholder={t("settings.Comfort tool description")}
                            resize="none"
                            className="min-h-24"
                            disabled={disabled}
                            onCollaborativeValueResetReady={onDescriptionResetReady}
                            onValueChange={onDescriptionChange}
                        />
                    ) : (
                        <Textarea
                            value={description}
                            placeholder={t("settings.Comfort tool description")}
                            resize="none"
                            className="min-h-24"
                            disabled={disabled}
                            onChange={(event) => onDescriptionChange(event.target.value)}
                        />
                    )}
                    {errors.description ? (
                        <Box textSize="sm" className="text-destructive">
                            {errors.description}
                        </Box>
                    ) : null}
                    {editingName ? (
                        <CollaborativeApiNamesSelect
                            apiNames={apiNames}
                            apiSelections={apiSelections}
                            disabled={disabled}
                            editingName={editingName}
                            open={open}
                            onApiNamesChange={onApiNamesChange}
                            onApiNamesRemoteChange={onApiNamesRemoteChange}
                            onApiNamesResetReady={onApiNamesResetReady}
                        />
                    ) : (
                        <MultiSelect
                            placeholder={t("settings.Select API(s) to include")}
                            selections={apiSelections}
                            selectedValue={apiNames}
                            className="api-comfort-tool-api-select"
                            listClassName="absolute w-[calc(100%_-_theme(spacing.12))]"
                            badgeListClassName="max-h-36 overflow-y-auto relative"
                            inputClassName="sticky bottom-0 bg-background ml-0 pl-2"
                            onValueChange={onApiNamesChange}
                            disabled={disabled}
                        />
                    )}
                    {errors.api_names ? (
                        <Box textSize="sm" className="text-destructive">
                            {errors.api_names}
                        </Box>
                    ) : null}
                </Flex>
                <Dialog.Footer className="mt-6 flex-col gap-2 sm:justify-end sm:gap-0">
                    <Button variant="secondary" disabled={disabled} onClick={onClose}>
                        {t("common.Cancel")}
                    </Button>
                    <SubmitButton type="button" isValidating={disabled} onClick={onSave}>
                        {editingName ? t("common.Save") : t("common.Create")}
                    </SubmitButton>
                </Dialog.Footer>
            </Dialog.Content>
        </Dialog.Root>
    );
}

export default ApiComfortToolDialog;

interface ICollaborativeApiNamesSelectProps {
    apiNames: string[];
    apiSelections: IMultiSelectItem[];
    disabled: bool;
    editingName: string;
    open: bool;
    onApiNamesChange: (apiNames: string[]) => void;
    onApiNamesRemoteChange: (apiNames: string[]) => void;
    onApiNamesResetReady: (resetValue: ((value: string) => void) | null) => void;
}

function CollaborativeApiNamesSelect({
    apiNames,
    apiSelections,
    disabled,
    editingName,
    open,
    onApiNamesChange,
    onApiNamesRemoteChange,
    onApiNamesResetReady,
}: ICollaborativeApiNamesSelectProps) {
    const [t] = useTranslation();
    const apiNamesCollaboration = useCollaborativeText({
        collaborationType: EEditorCollaborationType.AppSettings,
        uid: editingName,
        section: API_COMFORT_TOOL_COLLABORATION_SECTION,
        field: "api_names",
        defaultValue: JSON.stringify(apiNames),
        disabled: disabled || !open,
        onValueChange: (value) => {
            try {
                const nextApiNames = JSON.parse(value);
                if (!Array.isArray(nextApiNames) || nextApiNames.some((apiName) => typeof apiName !== "string")) {
                    return;
                }

                onApiNamesRemoteChange(nextApiNames);
            } catch {
                return;
            }
        },
    });
    const remoteApiMetaMap = useMemo(() => {
        return apiNamesCollaboration.remoteMeta.reduce<Record<string, IRemoteApiMetaState>>((acc, meta) => {
            const value = meta.value;
            if (
                !value ||
                !Utils.Type.isObject(value) ||
                !Utils.Type.isString((value as Record<string, unknown>).apiName) ||
                !Utils.Type.isBool((value as Record<string, unknown>).added) ||
                !Utils.Type.isNumber((value as Record<string, unknown>).updatedAt)
            ) {
                return acc;
            }

            const parsedValue = value as IApiSelectionMeta;
            const previous = acc[parsedValue.apiName];
            if (!previous || previous.updatedAt < parsedValue.updatedAt) {
                acc[parsedValue.apiName] = {
                    actorName: meta.name,
                    added: parsedValue.added,
                    borderColor: meta.color,
                    updatedAt: parsedValue.updatedAt,
                };
            }

            return acc;
        }, {});
    }, [apiNamesCollaboration.remoteMeta]);

    useEffect(() => {
        onApiNamesResetReady(apiNamesCollaboration.resetValue);

        return () => {
            onApiNamesResetReady(null);
        };
    }, [apiNamesCollaboration.resetValue, onApiNamesResetReady]);

    const changeApiNames = (nextApiNames: string[]) => {
        const addedApiName = nextApiNames.find((apiName) => !apiNames.includes(apiName)) ?? "";
        const removedApiName = apiNames.find((apiName) => !nextApiNames.includes(apiName)) ?? "";

        onApiNamesChange(nextApiNames);
        apiNamesCollaboration.updateMeta(
            addedApiName || removedApiName
                ? ({
                      added: !!addedApiName,
                      apiName: addedApiName || removedApiName,
                      updatedAt: Date.now(),
                  } satisfies IApiSelectionMeta)
                : null
        );
        apiNamesCollaboration.updateValue(JSON.stringify(nextApiNames));
    };

    return (
        <MultiSelect
            placeholder={t("settings.Select API(s) to include")}
            selections={apiSelections}
            selectedValue={apiNames}
            className="api-comfort-tool-api-select"
            listClassName="absolute w-[calc(100%_-_theme(spacing.12))]"
            badgeListClassName="max-h-36 overflow-y-auto relative"
            inputClassName="sticky bottom-0 bg-background ml-0 pl-2"
            onValueChange={changeApiNames}
            createBadgeWrapper={(badge, value) => {
                const remoteApiMeta = remoteApiMetaMap[value];

                return (
                    <span className="relative inline-flex">
                        {remoteApiMeta?.added ? (
                            <CollaborativeUserLabel
                                className="absolute left-1 top-0 z-[9999] -translate-y-1/2"
                                color={remoteApiMeta.borderColor}
                                name={remoteApiMeta.actorName}
                            />
                        ) : null}
                        <Tooltip.Root>
                            <Tooltip.Trigger asChild>
                                <span
                                    className="inline-flex rounded-md border-2 border-transparent"
                                    style={remoteApiMeta?.added ? { borderColor: remoteApiMeta.borderColor } : undefined}
                                >
                                    {badge}
                                </span>
                            </Tooltip.Trigger>
                            <Tooltip.Content>{value}</Tooltip.Content>
                        </Tooltip.Root>
                    </span>
                );
            }}
            renderSelectableItem={(item) => {
                const remoteApiMeta = remoteApiMetaMap[item.value];
                if (!remoteApiMeta || remoteApiMeta.added) {
                    return item.label;
                }

                return (
                    <div className="relative w-full rounded-md border-2 px-2 py-1" style={{ borderColor: remoteApiMeta.borderColor }}>
                        <CollaborativeUserLabel
                            className="absolute left-2 top-0 z-[9999] -translate-y-1/2"
                            color={remoteApiMeta.borderColor}
                            name={remoteApiMeta.actorName}
                        />
                        <span>{item.label}</span>
                    </div>
                );
            }}
            disabled={disabled}
        />
    );
}
