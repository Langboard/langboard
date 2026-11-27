import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import PillList from "@/components/base/PillList";
import Toast from "@/components/base/Toast";
import useCreateApiComfortTool from "@/controllers/api/settings/schemas/useCreateApiComfortTool";
import useGetApiComfortToolList from "@/controllers/api/settings/schemas/useGetApiComfortToolList";
import useGetApiList from "@/controllers/api/settings/schemas/useGetApiList";
import useUpdateApiComfortTool from "@/controllers/api/settings/schemas/useUpdateApiComfortTool";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ApiComfortToolModel } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import ApiComfortToolDialog from "@/pages/SettingsPage/components/apiComfortTools/ApiComfortToolDialog";
import ApiComfortToolListItem from "@/pages/SettingsPage/components/apiComfortTools/ApiComfortToolListItem";
import {
    COMFORT_TOOL_NAME_PATTERN,
    createComfortToolDraft,
    createEmptyComfortToolDraft,
    type IApiComfortToolDraft,
} from "@/pages/SettingsPage/components/apiComfortTools/types";

interface IApiComfortToolListProps {
    createDialogOpen: bool;
    setCreateDialogOpen: React.Dispatch<React.SetStateAction<bool>>;
}

function ApiComfortToolList({ createDialogOpen, setCreateDialogOpen }: IApiComfortToolListProps) {
    const [t] = useTranslation();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const { mutateAsync: getApiListMutateAsync } = useGetApiList({ interceptToast: true });
    const { mutateAsync: getApiComfortToolListMutateAsync } = useGetApiComfortToolList({ interceptToast: true });
    const { mutateAsync: createApiComfortToolMutateAsync } = useCreateApiComfortTool({ interceptToast: true });
    const { mutateAsync: updateApiComfortToolMutateAsync } = useUpdateApiComfortTool({ interceptToast: true });
    const [apiList, setApiList] = useState<Record<string, string>>({});
    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingName, setEditingName] = useState<string>();
    const [deletingName, setDeletingName] = useState<string>();
    const [name, setName] = useState("");
    const [label, setLabel] = useState("");
    const [description, setDescription] = useState("");
    const [apiNames, setApiNames] = useState<string[]>([]);
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [comfortToolDrafts, setComfortToolDrafts] = useState<Record<string, IApiComfortToolDraft>>({});
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canCreate = hasRoleAction(SettingRole.EAction.ApiComfortToolCreate);
    const canUpdate = hasRoleAction(SettingRole.EAction.ApiComfortToolUpdate);
    const canDelete = hasRoleAction(SettingRole.EAction.ApiComfortToolDelete);
    const comfortToolModels = ApiComfortToolModel.Model.useModels(() => true);
    const comfortTools = useMemo(
        () =>
            Object.fromEntries(
                [...comfortToolModels].sort((a, b) => a.name.localeCompare(b.name)).map((comfortTool) => [comfortTool.name, comfortTool])
            ),
        [comfortToolModels]
    );
    const resetCollaborativeLabelRef = useRef<((value: string) => void) | null>(null);
    const resetCollaborativeNameRef = useRef<((value: string) => void) | null>(null);
    const resetCollaborativeDescriptionRef = useRef<((value: string) => void) | null>(null);
    const resetCollaborativeApiNamesRef = useRef<((value: string) => void) | null>(null);
    const apiSelections = useMemo(() => Object.keys(apiList).map((value) => ({ label: value, value })), [apiList]);
    const dialogOpen = createDialogOpen || editDialogOpen;
    const updateComfortToolDraft = useCallback(
        (draft: Partial<IApiComfortToolDraft>) => {
            if (!editingName) {
                return;
            }

            setComfortToolDrafts((prev) => ({
                ...prev,
                [editingName]: {
                    ...(prev[editingName] ?? createEmptyComfortToolDraft()),
                    ...draft,
                },
            }));
        },
        [editingName]
    );
    const resetForm = () => {
        setEditingName(undefined);
        setName("");
        setLabel("");
        setDescription("");
        setApiNames([]);
        setErrors({});
    };

    const closeDialog = () => {
        if (isValidating) {
            return;
        }

        setCreateDialogOpen(false);
        setEditDialogOpen(false);
        resetForm();
    };

    useEffect(() => {
        const fetchLists = async () => {
            const [nextApiList] = await Promise.all([getApiListMutateAsync({}), getApiComfortToolListMutateAsync({})]);
            setApiList(nextApiList || {});
        };

        fetchLists();
    }, []);

    useEffect(() => {
        if (!editingName || comfortTools[editingName]) {
            return;
        }

        setComfortToolDrafts((prev) => {
            const nextDrafts = { ...prev };
            delete nextDrafts[editingName];
            return nextDrafts;
        });
        closeDialog();
    }, [comfortTools, editingName]);

    useEffect(() => {
        if (!createDialogOpen || isValidating || !canCreate) {
            return;
        }

        const draft = createEmptyComfortToolDraft();
        setEditingName(undefined);
        setName(draft.name);
        setLabel(draft.label);
        setDescription(draft.description);
        setApiNames(draft.api_names);
        setErrors({});
    }, [canCreate, createDialogOpen, isValidating]);

    const validateName = (nextName: string) => {
        const trimmedName = nextName.trim();
        if (trimmedName && !COMFORT_TOOL_NAME_PATTERN.test(trimmedName)) {
            return t("settings.errors.invalid.comfort_tool_name");
        }
        if (trimmedName && apiList[trimmedName]) {
            return t("settings.errors.duplicated.comfort_tool_name");
        }
        if (trimmedName && trimmedName !== editingName && comfortTools[trimmedName]) {
            return t("settings.errors.duplicated.comfort_tool_name");
        }

        return "";
    };

    const changeName = (nextName: string) => {
        setName(nextName);
        if (editingName) {
            updateComfortToolDraft({ name: nextName });
        }
        setErrors((prev) => {
            const nextErrors = { ...prev };
            const nameError = validateName(nextName);
            if (nameError) {
                nextErrors.name = nameError;
            } else {
                delete nextErrors.name;
            }
            return nextErrors;
        });
    };

    const changeLabel = (nextLabel: string) => {
        setLabel(nextLabel);
        if (editingName) {
            updateComfortToolDraft({ label: nextLabel });
        }
    };

    const changeDescription = (nextDescription: string) => {
        setDescription(nextDescription);
        if (editingName) {
            updateComfortToolDraft({ description: nextDescription });
        }
    };

    const updateApiNames = (nextApiNames: string[]) => {
        setApiNames(nextApiNames);
        if (editingName) {
            updateComfortToolDraft({ api_names: nextApiNames });
        }
    };

    const changeApiNames = (nextApiNames: string[]) => {
        updateApiNames(nextApiNames);
        setErrors((prev) => {
            if (!prev.api_names || nextApiNames.length === 0) {
                return prev;
            }

            const nextErrors = { ...prev };
            delete nextErrors.api_names;
            return nextErrors;
        });
    };

    const validateForm = () => {
        const nextErrors: Record<string, string> = {};
        const nameError = validateName(name);
        if (nameError) {
            nextErrors.name = nameError;
        }
        if (!label.trim()) {
            nextErrors.label = t("settings.errors.missing.comfort_tool_label");
        }
        if (!description.trim()) {
            nextErrors.description = t("settings.errors.missing.comfort_tool_description");
        }
        if (!apiNames.length) {
            nextErrors.api_names = t("settings.errors.missing.comfort_tool_api_names");
        }

        setErrors(nextErrors);
        return Object.keys(nextErrors).length === 0;
    };

    const saveComfortTool = async () => {
        if (isValidating || !validateForm() || (editingName ? !canUpdate : !canCreate)) {
            return;
        }

        setIsValidating(true);
        try {
            const savedName = name.trim();
            if (editingName) {
                await updateApiComfortToolMutateAsync({
                    comfort_tool_name: editingName,
                    name,
                    label,
                    description,
                    api_names: apiNames,
                });
            } else {
                await createApiComfortToolMutateAsync({
                    name,
                    label,
                    description,
                    api_names: apiNames,
                });
            }
            if (editingName) {
                resetCollaborativeLabelRef.current?.(label);
                resetCollaborativeNameRef.current?.(savedName);
                resetCollaborativeDescriptionRef.current?.(description);
                resetCollaborativeApiNamesRef.current?.(JSON.stringify(apiNames));
            }
            if (editingName) {
                setComfortToolDrafts((prev) => {
                    const nextDrafts = { ...prev };
                    delete nextDrafts[editingName];
                    delete nextDrafts[savedName];
                    return nextDrafts;
                });
            }
            setCreateDialogOpen(false);
            setEditDialogOpen(false);
            resetForm();
            Toast.Add.success(
                editingName ? t("successes.API comfort tool updated successfully.") : t("successes.API comfort tool created successfully.")
            );
        } finally {
            setIsValidating(false);
        }
    };

    const editComfortTool = (comfortToolName: string, comfortTool: ApiComfortToolModel.TModel) => {
        if (isValidating || !canUpdate) {
            return;
        }

        const draft = comfortToolDrafts[comfortToolName] ?? createComfortToolDraft(comfortToolName, comfortTool);
        setEditingName(comfortToolName);
        setName(draft.name);
        setLabel(draft.label);
        setDescription(draft.description);
        setApiNames(draft.api_names);
        setErrors({});
        setEditDialogOpen(true);
    };

    const cleanupDeletedComfortTool = (comfortToolName: string) => {
        setComfortToolDrafts((prev) => {
            const nextDrafts = { ...prev };
            delete nextDrafts[comfortToolName];
            return nextDrafts;
        });
        if (editingName === comfortToolName) {
            resetForm();
            setEditDialogOpen(false);
        }
        setDeletingName(undefined);
    };

    return (
        <>
            <Flex direction="col" gap="4">
                <PillList.Root>
                    {Object.keys(comfortTools).length ? (
                        Object.entries(comfortTools).map(([comfortToolName, comfortTool]) => (
                            <ApiComfortToolListItem
                                key={`api-comfort-tool-${comfortToolName}`}
                                canDelete={canDelete}
                                canUpdate={canUpdate}
                                comfortTool={comfortTool}
                                comfortToolName={comfortToolName as string}
                                deletingName={deletingName}
                                isValidating={isValidating}
                                onDeleted={cleanupDeletedComfortTool}
                                onEdit={editComfortTool}
                                setDeletingName={setDeletingName}
                            />
                        ))
                    ) : (
                        <Box rounded border className="p-4 text-muted-foreground">
                            {t("settings.No API comfort tools")}
                        </Box>
                    )}
                </PillList.Root>
            </Flex>
            <ApiComfortToolDialog
                apiNames={apiNames}
                apiSelections={apiSelections}
                description={description}
                disabled={isValidating}
                editingName={editingName}
                errors={errors}
                label={label}
                name={name}
                open={dialogOpen}
                onApiNamesChange={changeApiNames}
                onApiNamesRemoteChange={updateApiNames}
                onApiNamesResetReady={(resetValue) => {
                    resetCollaborativeApiNamesRef.current = resetValue;
                }}
                onClose={closeDialog}
                onDescriptionChange={changeDescription}
                onDescriptionResetReady={(resetValue) => {
                    resetCollaborativeDescriptionRef.current = resetValue;
                }}
                onLabelChange={changeLabel}
                onLabelResetReady={(resetValue) => {
                    resetCollaborativeLabelRef.current = resetValue;
                }}
                onNameChange={changeName}
                onNameResetReady={(resetValue) => {
                    resetCollaborativeNameRef.current = resetValue;
                }}
                onOpenChange={(open) => {
                    if (!open) {
                        closeDialog();
                    }
                }}
                onSave={saveComfortTool}
            />
        </>
    );
}

export default ApiComfortToolList;
