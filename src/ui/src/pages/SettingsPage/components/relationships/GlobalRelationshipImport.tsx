import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import SubmitButton from "@/components/base/SubmitButton";
import Table from "@/components/base/Table";
import Toast from "@/components/base/Toast";
import Tooltip from "@/components/base/Tooltip";
import { GlobalRelationshipType } from "@/core/models";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { Utils } from "@langboard/core/utils";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import mimeTypes from "react-native-mime-types";
import Papa from "papaparse";
import { IConvertedGlobalRelationshipType, IImportExportGlobalRelationshipType } from "@/pages/SettingsPage/components/relationships/types";
import { cn } from "@/core/utils/ComponentUtils";
import useImportGlobalRelationships from "@/controllers/api/settings/relationships/useImportGlobalRelationships";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { EHttpStatus } from "@langboard/core/enums";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { convertImportedTypeToModel } from "@/pages/SettingsPage/components/relationships/utils";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";

function GlobalRelationshipImport() {
    const [t] = useTranslation();
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const { isValidating, currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const [parsedRelationships, setParsedRelationships] = useState<IConvertedGlobalRelationshipType[]>();
    const [isOpened, setIsOpened] = useState(false);
    const handleFileChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            if (!e.target.files?.length) {
                return;
            }

            const file = e.target.files[0];
            e.target.value = "";

            const mimeType = mimeTypes.lookup(file.name);
            if (!["application/json", "text/csv"].includes(mimeType || "")) {
                Toast.Add.error(t("settings.Please upload a JSON or CSV file."));
                return;
            }

            const reader = new FileReader();
            reader.onload = (event) => {
                const content = event.target?.result;
                if (!Utils.Type.isString(content)) {
                    Toast.Add.error(t("settings.Error occurred while reading the file."));
                    return;
                }

                const isJson = mimeType === "application/json";
                const newRelationships: IConvertedGlobalRelationshipType[] = [];
                try {
                    if (isJson) {
                        const json: IImportExportGlobalRelationshipType[] = JSON.parse(content);
                        if (!Utils.Type.isArray(json)) {
                            throw new Error("Invalid JSON format");
                        }

                        for (let i = 0; i < json.length; ++i) {
                            const data = json[i];
                            if (!data.parent || !data.child) {
                                continue;
                            }

                            newRelationships.push(convertImportedTypeToModel(data));
                        }
                    } else {
                        const csv = Papa.parse(content);

                        for (let i = 0; i < csv.data.length; ++i) {
                            const row = csv.data[i] as string[];
                            if (row.length < 2) {
                                continue;
                            }

                            newRelationships.push({
                                parent_name: row[0],
                                child_name: row[1],
                                description: row[2] || "",
                            });
                        }
                    }
                } catch {
                    Toast.Add.error(t("settings.Error occurred while reading the file."));
                    return;
                }

                setParsedRelationships(() => newRelationships);
                setIsOpened(true);
            };

            reader.readAsText(file);
        },
        [parsedRelationships, setIsOpened]
    );

    useEffect(() => {
        if (!isOpened) {
            setParsedRelationships(() => undefined);
        }
    }, [isOpened]);

    if (!hasRoleAction(SettingRole.EAction.GlobalRelationshipCreate)) {
        return null;
    }

    return (
        <Flex>
            <Input
                type="file"
                disabled={isValidating}
                accept=".json, .csv"
                wrapperProps={{ className: "hidden" }}
                onChange={handleFileChange}
                ref={fileInputRef}
            />
            <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={() => fileInputRef.current?.click()}>
                <IconComponent icon="upload" size="4" />
                {t("settings.Import")}
            </Button>
            <GlobalRelationshipImportDialog isOpened={isOpened} setIsOpened={setIsOpened} parsedRelationships={parsedRelationships} />
        </Flex>
    );
}

interface IGlobalRelationshipImportDialogProps {
    isOpened: bool;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
    parsedRelationships?: IConvertedGlobalRelationshipType[];
}

function GlobalRelationshipImportDialog({ isOpened, setIsOpened, parsedRelationships }: IGlobalRelationshipImportDialogProps) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const globalRelationships = GlobalRelationshipType.Model.useModels(() => true);
    const { isValidating, setIsValidating } = useAppSetting();
    const { mutate: importGlobalRelationshipsMutate } = useImportGlobalRelationships();

    const save = () => {
        if (isValidating || !parsedRelationships?.length) {
            return;
        }

        setIsValidating(true);

        importGlobalRelationshipsMutate(
            {
                relationships: parsedRelationships,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected global relationship types deleted successfully."));
                    setIsOpened(() => false);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    return (
        <Dialog.Root open={isOpened && !!parsedRelationships} onOpenChange={setIsOpened}>
            <Dialog.Content className="w-full max-w-screen-sm" aria-describedby="">
                <Dialog.Title>{t("settings.Imported Relationships")}</Dialog.Title>
                <Table.Root className="mt-4">
                    <Table.Header>
                        <Table.Row>
                            <Table.Head className="w-1/4 p-2 text-center">{t("settings.Parent name")}</Table.Head>
                            <Table.Head className="w-1/4 p-2 text-center">{t("settings.Child name")}</Table.Head>
                            <Table.Head className="w-1/2 p-2 text-center">{t("settings.Description")}</Table.Head>
                        </Table.Row>
                    </Table.Header>
                    <Table.Body>
                        {parsedRelationships?.map((relationship, index) => (
                            <GlobalRelationshipImportDialogTableRow
                                key={`global-relationship-import-${relationship.parent_name}-${relationship.child_name}-${index}`}
                                globalRelationships={globalRelationships}
                                parsedRelationship={relationship}
                            />
                        ))}
                    </Table.Body>
                </Table.Root>
                <Flex items="center" justify="end" gap="1" mt="2">
                    <Button type="button" variant="secondary" size="sm" disabled={isValidating} onClick={() => setIsOpened(false)}>
                        {t("common.Cancel")}
                    </Button>
                    <SubmitButton type="button" size="sm" onClick={save} isValidating={isValidating}>
                        {t("common.Save")}
                    </SubmitButton>
                </Flex>
            </Dialog.Content>
        </Dialog.Root>
    );
}

interface IGlobalRelationshipImportDialogTableRowProps {
    globalRelationships: GlobalRelationshipType.TModel[];
    parsedRelationship: IConvertedGlobalRelationshipType;
}

function GlobalRelationshipImportDialogTableRow({ globalRelationships, parsedRelationship }: IGlobalRelationshipImportDialogTableRowProps) {
    const [parent, setParent] = useState(parsedRelationship.parent_name);
    const [child, setChild] = useState(parsedRelationship.child_name);
    const [description, setDescription] = useState(parsedRelationship.description || "");
    const [exists, setExists] = useState(false);
    const warningClassName = "bg-warning text-warning-foreground hover:bg-warning/70";

    useEffect(() => {
        parsedRelationship.parent_name = parent;
        parsedRelationship.child_name = child;
        parsedRelationship.description = description;

        for (let i = 0; i < globalRelationships.length; ++i) {
            const globalRelationship = globalRelationships[i];
            if (globalRelationship.parent_name === parent && globalRelationship.child_name === child) {
                setExists(true);
                return;
            }
        }

        setExists(false);
    }, [parent, child, description]);

    return (
        <Table.Row className={cn(exists && warningClassName)}>
            <Table.Cell className="p-2">
                <GlobalRelationshipImportDialogTableRowInput value={parent} setValue={setParent} exists={exists} align="start" />
            </Table.Cell>
            <Table.Cell className="p-2">
                <GlobalRelationshipImportDialogTableRowInput value={child} setValue={setChild} exists={exists} align="center" />
            </Table.Cell>
            <Table.Cell className="p-2">
                <GlobalRelationshipImportDialogTableRowInput value={description} setValue={setDescription} exists={exists} align="end" />
            </Table.Cell>
        </Table.Row>
    );
}

interface IGlobalRelationshipImportDialogTableRowInputProps {
    value: string;
    setValue: React.Dispatch<React.SetStateAction<string>>;
    exists: bool;
    align: React.ComponentProps<typeof Tooltip.Content>["align"];
}

function GlobalRelationshipImportDialogTableRowInput({ value, setValue, exists, align }: IGlobalRelationshipImportDialogTableRowInputProps) {
    const [t] = useTranslation();
    const [isTooltipOpened, setIsTooltipOpened] = useState(false);
    const warningClassName = "bg-warning text-warning-foreground hover:bg-warning/70";

    return (
        <Tooltip.Root open={isTooltipOpened && exists}>
            <Tooltip.Trigger asChild>
                <Input
                    value={value}
                    variant={exists ? "warning" : "default"}
                    onChange={(e) => setValue(e.target.value)}
                    onFocus={() => setIsTooltipOpened(true)}
                    onBlur={() => setIsTooltipOpened(false)}
                />
            </Tooltip.Trigger>
            <Tooltip.Content side="top" align={align} className={cn("border-warning-border", warningClassName)}>
                {t("settings.This relationship already exists and will duplicate upon import.")}
            </Tooltip.Content>
        </Tooltip.Root>
    );
}

export default GlobalRelationshipImport;
