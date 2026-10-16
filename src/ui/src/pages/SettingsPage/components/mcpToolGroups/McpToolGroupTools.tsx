import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Popover from "@/components/base/Popover";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import Tooltip from "@/components/base/Tooltip";
import MarkdownCodeBlock from "@/components/Markdown/CodeBlock";
import MultiSelect from "@/components/MultiSelect";
import useUpdateMcpToolGroup from "@/controllers/api/settings/mcpToolGroups/useUpdateMcpToolGroup";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { McpRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { ROUTES } from "@/core/routing/constants";
import { useMcpTools } from "@/core/stores/McpToolStore";
import { cn } from "@/core/utils/ComponentUtils";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const McpToolGroupTools = memo(() => {
    const [t] = useTranslation();
    const { model: toolGroup } = ModelRegistry.McpToolGroup.useContext();
    const allMcpTools = useMcpTools();
    const tools = toolGroup.useField("tools");

    const toolDetails = tools.map((toolName) => allMcpTools[toolName]).filter((tool) => tool !== undefined);

    return (
        <Flex direction="col" gap="3">
            <Flex justify="between" items="center">
                <h3 className="text-sm font-semibold">{t("mcp.Available Tools ({count})", { count: toolDetails.length })}</h3>
                <McpToolGroupToolsEdit />
            </Flex>
            {toolDetails.map((tool) => (
                <Flex key={tool.name} direction="col" gap="2" className="w-full rounded-md border p-3">
                    <Flex direction="col" gap="1">
                        <h4 className="text-sm font-medium">{tool.name}</h4>
                        <p className="text-xs text-muted-foreground">{tool.description}</p>
                    </Flex>
                    <details className="group">
                        <summary className="cursor-pointer text-xs text-primary hover:underline">{t("mcp.View Input Schema")}</summary>
                        <Box w="full" p="3" className="[&_.prose]:!max-w-full">
                            <MarkdownCodeBlock code={JSON.stringify(tool.input_schema, null, 2)} language="json" />
                        </Box>
                    </details>
                </Flex>
            ))}
        </Flex>
    );
});

function McpToolGroupToolsEdit() {
    const [t] = useTranslation();
    const { model: toolGroup } = ModelRegistry.McpToolGroup.useContext();
    const { currentUser } = useAppSetting();
    const mcpRoleActions = currentUser.useField("mcp_role_actions");
    const { hasRoleAction } = useRoleActionFilter(mcpRoleActions);
    const canUpdateMcpToolGroup = hasRoleAction(McpRole.EAction.Update);
    const { isValidating, setIsValidating } = useAppSetting();
    const allMcpTools = useMcpTools();
    const navigate = usePageNavigateRef();
    const tools = toolGroup.useField("tools");
    const [selectedTools, setSelectedTools] = useState(tools);
    const [isOpened, setIsOpened] = useState(false);
    const { mutateAsync } = useUpdateMcpToolGroup(toolGroup, { interceptToast: true });

    const save = () => {
        if (!canUpdateMcpToolGroup) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            tools: selectedTools,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.MCP tool group tools changed successfully.");
            },
            finally: () => {
                setIsOpened(false);
                setIsValidating(false);
            },
        });
    };

    return (
        <Popover.Root open={isOpened} onOpenChange={setIsOpened}>
            <Popover.Trigger asChild>
                <Button variant="outline" size="sm" disabled={!canUpdateMcpToolGroup}>
                    {t("common.Edit")}
                </Button>
            </Popover.Trigger>
            <Popover.Content align="end" className="w-full">
                <Flex direction="col" gap="2">
                    <MultiSelect
                        placeholder={t("settings.Select tool(s) to use")}
                        selections={Object.keys(allMcpTools).map((value) => ({ label: value, value }))}
                        selectedValue={selectedTools}
                        className={cn(
                            "max-w-[calc(100vw_-_theme(spacing.20))]",
                            "sm:max-w-[calc(theme(screens.sm)__-_theme(spacing.60))]",
                            "lg:max-w-[calc(theme(screens.md)__-_theme(spacing.60))]",
                            "min-w-[min(theme(spacing.20),100%)]"
                        )}
                        listClassName="absolute w-[calc(100%_-_theme(spacing.6))]"
                        badgeListClassName="max-h-28 overflow-y-auto relative"
                        inputClassName="sticky bottom-0 bg-background ml-0 pl-2"
                        onValueChange={setSelectedTools}
                        createBadgeWrapper={(badge, value) => (
                            <Tooltip.Root>
                                <Tooltip.Trigger asChild>{badge}</Tooltip.Trigger>
                                <Tooltip.Content className="max-w-[min(95vw,theme(spacing.96))]">{allMcpTools[value]?.name}</Tooltip.Content>
                            </Tooltip.Root>
                        )}
                        disabled={!canUpdateMcpToolGroup || isValidating}
                    />
                    <Flex items="center" justify="end" gap="1" mt="2">
                        <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={!canUpdateMcpToolGroup || isValidating}
                            onClick={() => setIsOpened(false)}
                        >
                            {t("common.Cancel")}
                        </Button>
                        <SubmitButton type="button" size="sm" onClick={save} isValidating={isValidating}>
                            {t("common.Save")}
                        </SubmitButton>
                    </Flex>
                </Flex>
            </Popover.Content>
        </Popover.Root>
    );
}

export default McpToolGroupTools;
