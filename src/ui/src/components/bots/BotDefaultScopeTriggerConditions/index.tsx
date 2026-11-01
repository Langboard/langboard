import Box from "@/components/base/Box";
import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import Toast from "@/components/base/Toast";
import { BotDefaultScopeBranchModel } from "@/core/models";
import useUpdateBotDefaultScopeBranch from "@/controllers/api/settings/bots/useUpdateBotDefaultScopeBranch";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { EHttpStatus } from "@langboard/core/enums";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { useTranslation } from "react-i18next";
import { memo, useCallback, useEffect, useState } from "react";
import { DomainEventType } from "@/core/events/DomainEventType";
import { Utils } from "@langboard/core/utils";
import { CheckedState } from "@radix-ui/react-checkbox";
import { cn } from "@/core/utils/ComponentUtils";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";

export interface IBotDefaultScopeTriggerConditionsProps {
    branch: BotDefaultScopeBranchModel.TModel;
}

const BotDefaultScopeTriggerConditions = memo(({ branch }: IBotDefaultScopeTriggerConditionsProps) => {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const [isUpdating, setIsUpdating] = useState(false);
    const { mutateAsync: updateBotDefaultScopeBranchMutateAsync } = useUpdateBotDefaultScopeBranch(branch.uid, { interceptToast: true });
    const conditionsMap = branch.useField("conditions_map");

    const updateCondition = useCallback(
        (target_table: TBotRelatedTargetTable, condition: DomainEventType, enabled: bool) => {
            if (isUpdating) {
                return;
            }

            setIsUpdating(true);

            const currentConditions = conditionsMap?.[target_table] || [];
            const newConditions = enabled
                ? [...currentConditions, condition].filter((c, i, arr) => arr.indexOf(c) === i)
                : currentConditions.filter((c) => c !== condition);

            const promise = updateBotDefaultScopeBranchMutateAsync({
                conditions_map: {
                    ...conditionsMap,
                    [target_table]: newConditions,
                },
            });

            Toast.Add.promise(promise, {
                loading: t("common.Updating..."),
                error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
                success: () => t("successes.Bot default trigger conditions updated successfully."),
                finally: () => {
                    setIsUpdating(false);
                },
            });
        },
        [isUpdating, conditionsMap]
    );

    return (
        <Flex direction="col" gap="4">
            <Box textSize="base" weight="medium">
                {t("settings.Trigger Conditions")}
            </Box>

            <Flex direction="col" gap="3">
                {Object.entries(BOT_SCOPES).map(([target_table, botScope]) => (
                    <Flex direction="col" gap="2" key={`${branch.uid}-${target_table}`}>
                        <Box textSize="sm" weight="medium">
                            {t(`bot.target_tables.${target_table}`)}
                        </Box>
                        {Object.keys(botScope.CATEGORIZED_EVENT_TYPES).map((category) => (
                            <Flex direction="col" gap="1" key={`${branch.uid}-${target_table}-${category}`}>
                                <Box textSize="xs" className="text-muted-foreground">
                                    {t(`botTriggerCondition.${category}.Category`)}
                                </Box>
                                <Flex wrap gap="2">
                                    {botScope.CATEGORIZED_EVENT_TYPES[category].map((condition) => (
                                        <TriggerConditionCheckbox
                                            key={`${branch.uid}-${target_table}-${category}-${condition}`}
                                            targetTable={target_table}
                                            category={category}
                                            conditionsMap={conditionsMap}
                                            condition={condition}
                                            isUpdating={isUpdating}
                                            onUpdate={updateCondition}
                                        />
                                    ))}
                                </Flex>
                            </Flex>
                        ))}
                    </Flex>
                ))}
            </Flex>
        </Flex>
    );
});
BotDefaultScopeTriggerConditions.displayName = "BotDefaultScopeTriggerConditions";

interface ITriggerConditionCheckboxProps {
    targetTable: TBotRelatedTargetTable;
    category: string;
    conditionsMap: BotDefaultScopeBranchModel.Interface["conditions_map"];
    condition: DomainEventType;
    isUpdating: bool;
    onUpdate: (targetTable: TBotRelatedTargetTable, condition: DomainEventType, enabled: bool) => void;
}

function TriggerConditionCheckbox({ targetTable, category, condition, conditionsMap, isUpdating, onUpdate }: ITriggerConditionCheckboxProps) {
    const [t] = useTranslation();
    const [isChecked, setIsChecked] = useState((conditionsMap?.[targetTable] || []).includes(condition));

    useEffect(() => {
        setIsChecked((conditionsMap?.[targetTable] || []).includes(condition));
    }, [conditionsMap, targetTable, condition]);

    const handleCheckedChange = useCallback(
        (checked: CheckedState) => {
            if (Utils.Type.isString(checked) || isUpdating) {
                return;
            }
            onUpdate(targetTable, condition, checked);
        },
        [isUpdating, condition, targetTable, onUpdate]
    );

    return (
        <Label
            display="flex"
            items="center"
            gap="3"
            p="2"
            cursor={isUpdating ? "not-allowed" : "pointer"}
            className={cn("transition-all", isUpdating ? "opacity-70" : "hover:text-foreground/70")}
        >
            <Checkbox checked={isChecked} onCheckedChange={handleCheckedChange} disabled={isUpdating} />
            <Box>{t(`botTriggerCondition.${category}.conditions.${condition}`)}</Box>
        </Label>
    );
}

export default BotDefaultScopeTriggerConditions;
