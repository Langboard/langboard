import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import Collaborative from "@/components/Collaborative";
import useUpdateMcpToolGroup from "@/controllers/api/settings/mcpToolGroups/useUpdateMcpToolGroup";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { McpRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useRef } from "react";
import { useTranslation } from "react-i18next";

const McpToolGroupDescription = memo(() => {
    const [t] = useTranslation();
    const { model: toolGroup } = ModelRegistry.McpToolGroup.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction } = useRoleActionFilter(mcpRoleActions);
    const canUpdateMcpToolGroup = hasRoleAction(McpRole.EAction.Update);
    const description = toolGroup.useField("description");
    const editorName = `${toolGroup.uid}-tool-group-description`;
    const { mutateAsync } = useUpdateMcpToolGroup(toolGroup, { interceptToast: true });
    const resetCollaborativeDescriptionRef = useRef<((value: string) => void) | null>(null);

    const { valueRef, isEditing, setIsEditing, changeMode } = useChangeEditMode({
        canEdit: () => canUpdateMcpToolGroup,
        valueType: "input",
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
                    return t("successes.MCP tool group description changed successfully.");
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
        <Box>
            {!isEditing ? (
                <Flex
                    items="center"
                    cursor={canUpdateMcpToolGroup ? "pointer" : "default"}
                    textSize="sm"
                    weight="semibold"
                    onClick={() => changeMode("edit")}
                >
                    <Box as="span" className="max-w-[calc(100%_-_theme(spacing.6))] truncate">
                        {description}
                    </Box>
                    {canUpdateMcpToolGroup && <IconComponent icon="pencil" size="4" className="ml-2" />}
                </Flex>
            ) : (
                <Flex items="center" gap="1">
                    <Collaborative.Input
                        collaborationType={EEditorCollaborationType.AppSettings}
                        uid={toolGroup.uid}
                        section="mcp-tool-group"
                        field="description"
                        ref={valueRef}
                        className={cn(
                            "h-5 rounded-none border-x-0 border-t-0 bg-transparent p-0 text-sm font-semibold scrollbar-hide",
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
        </Box>
    );
});

export default McpToolGroupDescription;
