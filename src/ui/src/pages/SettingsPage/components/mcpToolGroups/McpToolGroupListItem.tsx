import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import PillList from "@/components/base/PillList";
import Popover from "@/components/base/Popover";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useDeleteMcpToolGroup from "@/controllers/api/settings/mcpToolGroups/useDeleteMcpToolGroup";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { McpRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { IMcpToolGroupListItemContextParams } from "@/pages/SettingsPage/components/mcpToolGroups/types";
import { EHttpStatus } from "@langboard/core/enums";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import McpToolGroupActivation from "./McpToolGroupActivation";

function McpToolGroupListItem() {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const {
        model: toolGroup,
        params: { viewGroupUID, setViewGroupUID, selectedGroups, setSelectedGroups },
    } = ModelRegistry.McpToolGroup.useContext<IMcpToolGroupListItemContextParams>();
    const { currentUser } = useAppSetting();
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction } = useRoleActionFilter(mcpRoleActions);
    const canDeleteMcpToolGroup = hasRoleAction(McpRole.EAction.Delete);
    const { isValidating, setIsValidating } = useAppSetting();
    const { mutateAsync } = useDeleteMcpToolGroup(toolGroup, { interceptToast: true });
    const [isOpened, setIsOpened] = useState(false);
    const name = toolGroup.useField("name");
    const tools = toolGroup.useField("tools");
    const isView = viewGroupUID === toolGroup.uid;

    const deleteGroup = (e: React.MouseEvent<HTMLButtonElement>) => {
        e.preventDefault();
        e.stopPropagation();

        if (isValidating || !canDeleteMcpToolGroup) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.MCP tool group deleted successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const changeOpenState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        setIsOpened(opened);
    };

    return (
        <PillList.ItemRoot size="sm" className="cursor-pointer" onClick={() => setViewGroupUID(isView ? undefined : toolGroup.uid)}>
            <PillList.ItemTitle className="flex w-auto items-center gap-2">
                <Checkbox
                    checked={selectedGroups.some((value) => value === toolGroup.uid)}
                    onClick={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        setSelectedGroups((prev) => {
                            if (prev.some((value) => value === toolGroup.uid)) {
                                return prev.filter((value) => value !== toolGroup.uid);
                            } else {
                                return [...prev, toolGroup.uid];
                            }
                        });
                    }}
                />
                <Flex direction="col" gap="0.5" className="flex-1">
                    <Flex items="center" gap="2">
                        {name}
                        <Box textSize="xs" className="text-muted-foreground">
                            [{t("mcp.{count} tools", { count: tools.length })}]
                        </Box>
                    </Flex>
                </Flex>
            </PillList.ItemTitle>
            <PillList.ItemContent
                onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                }}
            >
                <Flex gap="1">
                    <McpToolGroupActivation toolGroup={toolGroup} variant="list" />
                    {canDeleteMcpToolGroup ? (
                        <Popover.Root open={isOpened} onOpenChange={changeOpenState}>
                            <Popover.Trigger asChild>
                                <Button variant="destructive" size="icon-sm" title={t("common.Delete")} titleSide="bottom" disabled={isValidating}>
                                    <IconComponent icon="trash-2" size="5" />
                                </Button>
                            </Popover.Trigger>
                            <Popover.Content align="end">
                                <Box mb="1" textSize={{ initial: "sm", sm: "base" }} weight="semibold" className="text-center">
                                    {t("ask.Are you sure you want to delete this MCP tool group?")}
                                </Box>
                                <Box maxW="full" textSize="sm" weight="bold" className="text-center text-red-500">
                                    {t("common.deleteDescriptions.All data will be lost.")}
                                </Box>
                                <Box maxW="full" textSize="sm" weight="bold" className="text-center text-red-500">
                                    {t("common.deleteDescriptions.This action cannot be undone.")}
                                </Box>
                                <Flex items="center" justify="end" gap="1" mt="2">
                                    <Button type="button" variant="secondary" size="sm" disabled={isValidating} onClick={() => setIsOpened(false)}>
                                        {t("common.Cancel")}
                                    </Button>
                                    <SubmitButton type="button" variant="destructive" size="sm" onClick={deleteGroup} isValidating={isValidating}>
                                        {t("common.Delete")}
                                    </SubmitButton>
                                </Flex>
                            </Popover.Content>
                        </Popover.Root>
                    ) : null}
                </Flex>
            </PillList.ItemContent>
        </PillList.ItemRoot>
    );
}

export default McpToolGroupListItem;
