import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import Select from "@/components/base/Select";
import Box from "@/components/base/Box";
import useApplyDefaultBotScope from "@/controllers/api/shared/botScopes/useApplyDefaultBotScope";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { EHttpStatus } from "@langboard/core/enums";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { useTranslation } from "react-i18next";
import { BotTriggerConditionListProvider, useBotTriggerConditionList } from "@/components/bots/BotTriggerConditionList/Provider";
import BotTriggerCondition from "@/components/bots/BotTriggerConditionList/Condition";
import { TBotScopeRelatedParams } from "@/controllers/api/shared/botScopes/types";
import * as BaseBotScopeModel from "@/core/models/botScopes/BaseBotScopeModel";
import { ModelRegistry, TBotScopeModel, TBotScopeModelName } from "@/core/models/ModelRegistry";
import { useMemo, useState } from "react";

export interface IBotTriggerConditionListProps {
    params: TBotScopeRelatedParams;
    botUID: string;
    botScope?: TBotScopeModel<TBotScopeModelName>;
}

function BotTriggerConditionList({ params, botUID, botScope }: IBotTriggerConditionListProps) {
    return (
        <BotTriggerConditionListProvider params={params} botUID={botUID} botScope={botScope}>
            <BotTriggerConditionListDisplay />
        </BotTriggerConditionListProvider>
    );
}

function BotTriggerConditionListDisplay() {
    const [t] = useTranslation();
    const { categories } = useBotTriggerConditionList();

    return (
        <Flex direction="col" gap="3">
            <BotTriggerConditionApplyDefaultButton />
            {Object.keys(categories).map((category) => (
                <Flex direction="col" gap={{ initial: "1", md: "2" }} key={`bot-trigger-category-${category}`}>
                    <Box>{t(`botTriggerCondition.${category}.Category`)}</Box>
                    <BotTriggerConditionCategory category={category} />
                </Flex>
            ))}
        </Flex>
    );
}

interface IBotTriggerConditionCategoryProps {
    category: string;
}

function BotTriggerConditionCategory({ category }: IBotTriggerConditionCategoryProps) {
    const { categories } = useBotTriggerConditionList();
    const conditions = categories[category];

    return (
        <Flex gap={{ initial: "1", md: "2" }} wrap>
            {conditions.map((condition) => (
                <BotTriggerCondition key={`bot-trigger-category-${category}-condition-${condition}`} category={category} conditionType={condition} />
            ))}
        </Flex>
    );
}

function BotTriggerConditionApplyDefaultButton() {
    const { botScope } = useBotTriggerConditionList();

    if (!botScope) {
        return <BotTriggerConditionApplyDefaultButtonWithoutBotScope />;
    }

    return <BotTriggerConditionApplyDefaultButtonWithBotScope botScope={botScope} />;
}

function BotTriggerConditionApplyDefaultButtonWithoutBotScope() {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { params, botUID } = useBotTriggerConditionList();
    const { mutateAsync } = useApplyDefaultBotScope({ bot_uid: botUID }, { interceptToast: true });
    const [isApplying, setIsApplying] = useState(false);
    const defaultScopes = ModelRegistry.BotDefaultScopeBranchModel.Model.useModels((model) => model.bot_uid === botUID);

    const defaultScopeOptions = useMemo(() => {
        return [
            { value: "custom", label: t("bot.Custom") },
            ...defaultScopes.map((scope) => ({
                value: scope.uid,
                label: scope.name,
            })),
        ];
    }, [defaultScopes]);

    const applyDefault = (defaultScopeUID?: string) => {
        if (isApplying) {
            return;
        }

        setIsApplying(true);
        const promise = mutateAsync({
            target_table: params.target_table,
            target_uid: params.target_uid,
            default_scope_branch_uid: defaultScopeUID === "custom" ? null : defaultScopeUID,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Bot default triggers applied successfully.");
            },
            finally: () => {
                setIsApplying(false);
            },
        });
    };

    return (
        <Flex justify="end" items="center" gap="2">
            <Select.Root
                value="custom"
                onValueChange={(value) => {
                    applyDefault(value);
                }}
                disabled={isApplying}
            >
                <Select.Trigger className="w-48">
                    <Select.Value placeholder={t("bot.Apply default scope branch")} />
                </Select.Trigger>
                <Select.Content>
                    {defaultScopeOptions.map((option) => (
                        <Select.Item key={option.value} value={option.value}>
                            {option.label}
                        </Select.Item>
                    ))}
                </Select.Content>
            </Select.Root>
        </Flex>
    );
}

interface IBotTriggerConditionApplyDefaultButtonWithBotScopeProps {
    botScope: TBotScopeModel<TBotScopeModelName>;
}

function BotTriggerConditionApplyDefaultButtonWithBotScope({ botScope }: IBotTriggerConditionApplyDefaultButtonWithBotScopeProps) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { params, botUID } = useBotTriggerConditionList();
    const { mutateAsync } = useApplyDefaultBotScope({ bot_uid: botUID }, { interceptToast: true });
    const [isApplying, setIsApplying] = useState(false);
    const defaultScopes = ModelRegistry.BotDefaultScopeBranchModel.Model.useModels((model) => model.bot_uid === botUID);
    const defaultScopeBranchUID = (botScope as BaseBotScopeModel.TModel).useField("default_scope_branch_uid");

    const defaultScopeOptions = useMemo(() => {
        return [
            { value: "custom", label: t("bot.Custom") },
            ...defaultScopes.map((scope) => ({
                value: scope.uid,
                label: scope.name,
            })),
        ];
    }, [defaultScopes]);

    const applyDefault = (defaultScopeUID?: string) => {
        if (isApplying) {
            return;
        }

        setIsApplying(true);
        const promise = mutateAsync({
            target_table: params.target_table,
            target_uid: params.target_uid,
            default_scope_branch_uid: defaultScopeUID === "custom" ? null : defaultScopeUID,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Bot default triggers applied successfully.");
            },
            finally: () => {
                setIsApplying(false);
            },
        });
    };

    return (
        <Flex justify="end" items="center" gap="2">
            <Select.Root
                value={defaultScopeBranchUID || "custom"}
                onValueChange={(value) => {
                    applyDefault(value);
                }}
                disabled={isApplying}
            >
                <Select.Trigger className="w-48">
                    <Select.Value placeholder={t("bot.Apply default scope branch")} />
                </Select.Trigger>
                <Select.Content>
                    {defaultScopeOptions.map((option) => (
                        <Select.Item key={option.value} value={option.value}>
                            {option.label}
                        </Select.Item>
                    ))}
                </Select.Content>
            </Select.Root>
        </Flex>
    );
}

export default BotTriggerConditionList;
