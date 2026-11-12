import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Tabs from "@/components/base/Tabs";
import Toast from "@/components/base/Toast";
import useGetMcpToolList from "@/controllers/api/mcp/getMcpToolList";
import useDeleteSelectedMcpToolGroups from "@/controllers/api/settings/mcpToolGroups/useDeleteSelectedMcpToolGroups";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { McpToolGroup } from "@/core/models";
import { McpRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import McpToolGroupList from "@/pages/SettingsPage/components/mcpToolGroups/McpToolGroupList";
import { EHttpStatus } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function McpServerPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const [activeTab, setActiveTab] = useState<McpToolGroup.TGroupType>("global");
    const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
    const { mutateAsync: getMcpToolListMutateAsync } = useGetMcpToolList();
    const { mutate: deleteSelectedMcpToolGroupsMutate } = useDeleteSelectedMcpToolGroups();
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction } = useRoleActionFilter(mcpRoleActions);
    const canDeleteMcpToolGroup = hasRoleAction(McpRole.EAction.Delete);

    useEffect(() => {
        setPageAliasRef.current("MCP Server");
        const fetchMcpTools = async () => {
            try {
                await getMcpToolListMutateAsync({});
            } catch (error) {
                const { handle } = setupApiErrorHandler({
                    [EHttpStatus.HTTP_403_FORBIDDEN]: {
                        after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                    },
                });

                handle(error);
            }
        };

        fetchMcpTools();
    }, []);

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_MCP_TOOL_GROUP(activeTab));
    };

    const deleteSelectedMcpToolGroups = () => {
        if (isValidating || !selectedGroups.length || !canDeleteMcpToolGroup) {
            return;
        }

        setIsValidating(true);

        deleteSelectedMcpToolGroupsMutate(
            {
                group_uids: selectedGroups,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected MCP tool groups deleted successfully."));
                    setSelectedGroups([]);
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
        <>
            <Flex justify="between" mb="4" pb="2" textSize="2xl" weight="semibold" className="scroll-m-20 flex-wrap gap-3">
                <span className="max-w-72">{t("mcp.MCP Server")}</span>
                <Flex gap="2" wrap justify="end">
                    {selectedGroups.length > 0 && canDeleteMcpToolGroup && (
                        <Button variant="destructive" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={deleteSelectedMcpToolGroups}>
                            <IconComponent icon="trash" size="4" />
                            {t("common.Delete")}
                        </Button>
                    )}
                    {hasRoleAction(McpRole.EAction.Create) && (
                        <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                            <IconComponent icon="plus" size="4" />
                            {t("settings.Add new")}
                        </Button>
                    )}
                </Flex>
            </Flex>
            <Tabs.Provider value={activeTab} onValueChange={(v) => setActiveTab(v as McpToolGroup.TGroupType)}>
                <Tabs.List>
                    <Tabs.Trigger value="global">{t("settings.Global")}</Tabs.Trigger>
                    <Tabs.Trigger value="admin">{t("settings.Admin")}</Tabs.Trigger>
                </Tabs.List>
                <Tabs.Content value="global">
                    <McpToolGroupList type="global" selectedGroups={selectedGroups} setSelectedGroups={setSelectedGroups} />
                </Tabs.Content>
                <Tabs.Content value="admin">
                    <McpToolGroupList type="admin" selectedGroups={selectedGroups} setSelectedGroups={setSelectedGroups} />
                </Tabs.Content>
            </Tabs.Provider>
        </>
    );
}
McpServerPage.displayName = "McpServerPage";

export default McpServerPage;
