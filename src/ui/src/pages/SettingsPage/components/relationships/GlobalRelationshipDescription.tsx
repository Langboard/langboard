import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Table from "@/components/base/Table";
import Toast from "@/components/base/Toast";
import Collaborative from "@/components/Collaborative";
import useUpdateGlobalRelationship from "@/controllers/api/settings/relationships/useUpdateGlobalRelationship";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

function GlobalRelationshipDescription() {
    const [t] = useTranslation();
    const { model: globalRelationship } = ModelRegistry.GlobalRelationshipType.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateGlobalRelationship = hasRoleAction(SettingRole.EAction.GlobalRelationshipUpdate);
    const description = globalRelationship.useField("description");
    const editorName = `${globalRelationship.uid}-global-relationship-description`;
    const { mutateAsync } = useUpdateGlobalRelationship(globalRelationship, { interceptToast: true });
    const resetCollaborativeDescriptionRef = useRef<((value: string) => void) | null>(null);

    const { valueRef, isEditing, setIsEditing, changeMode } = useChangeEditMode({
        canEdit: () => canUpdateGlobalRelationship,
        valueType: "input",
        canEmpty: true,
        editorName,
        save: (value, endCallback) => {
            const promise = mutateAsync({
                description: value,
            });

            Toast.Add.promise(promise, {
                loading: t("common.Changing..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler(
                        {
                            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                            },
                        },
                        messageRef
                    );

                    handle(error);
                    return messageRef.message;
                },
                success: () => {
                    return t("successes.Description changed successfully.");
                },
                finally: () => {
                    endCallback();
                },
            });
        },
        originalValue: description,
    });

    const cancelEditing = () => {
        resetCollaborativeDescriptionRef.current?.(description);
        setIsEditing(false);
    };

    return (
        <Table.FlexCell
            className={cn(
                "w-[calc(calc(100%_/_6_*_4)_-_theme(spacing.12))] truncate text-center",
                isEditing && "pb-2.5 pt-[calc(theme(spacing.4)_-_2px)]"
            )}
        >
            {!isEditing ? (
                <Flex
                    cursor={canUpdateGlobalRelationship ? "pointer" : "default"}
                    justify="center"
                    items="center"
                    gap="1"
                    position="relative"
                    onClick={() => changeMode("edit")}
                >
                    <Box
                        as="span"
                        className={cn("max-w-[calc(100%_-_theme(spacing.6))] truncate", !description?.length && "text-muted-foreground/70")}
                    >
                        {description?.length ? description : t("settings.No description")}
                    </Box>
                    {canUpdateGlobalRelationship && (
                        <Box position="relative">
                            <Box position="absolute" left="2" className="top-1/2 -translate-y-1/2">
                                <IconComponent icon="pencil" size="4" />
                            </Box>
                        </Box>
                    )}
                </Flex>
            ) : (
                <Flex items="center" gap="1">
                    <Collaborative.Input
                        collaborationType={EEditorCollaborationType.AppSettings}
                        uid={globalRelationship.uid}
                        section="global-relationship"
                        field="description"
                        ref={valueRef}
                        className={cn(
                            "h-6 rounded-none border-x-0 border-t-0 bg-transparent p-0 text-center scrollbar-hide",
                            "focus-visible:border-b-primary focus-visible:ring-0"
                        )}
                        defaultValue={description}
                        onCollaborativeValueResetReady={(resetValue) => {
                            resetCollaborativeDescriptionRef.current = resetValue;
                        }}
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                        }}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") {
                                e.preventDefault();
                                e.stopPropagation();
                                changeMode("view");
                                return;
                            }
                        }}
                    />
                    <Button type="button" size="icon-sm" variant="ghost" onClick={() => changeMode("view")} title={t("common.Save")}>
                        <IconComponent icon="check" size="4" />
                    </Button>
                    <Button type="button" size="icon-sm" variant="ghost" onClick={cancelEditing} title={t("common.Cancel")}>
                        <IconComponent icon="x" size="4" />
                    </Button>
                </Flex>
            )}
        </Table.FlexCell>
    );
}

export default GlobalRelationshipDescription;
