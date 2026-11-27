import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useCreateNotificationScheduleRule from "@/controllers/api/settings/notificationSchedule/useCreateNotificationScheduleRule";
import type { ICreateNotificationScheduleRuleForm } from "@/controllers/api/settings/notificationSchedule/useCreateNotificationScheduleRule";
import useDeleteSelectedNotificationScheduleRules from "@/controllers/api/settings/notificationSchedule/useDeleteSelectedNotificationScheduleRules";
import useGetNotificationSchedule from "@/controllers/api/settings/notificationSchedule/useGetNotificationSchedule";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { NotificationScheduleRuleModel } from "@/core/models";
import { unsubscribeModelSocketTopic } from "@/core/models/base/socketSubscriptions";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import NotificationScheduleForm from "@/pages/SettingsPage/components/notificationSchedule/NotificationScheduleForm";
import { getNotificationScheduleTimezone } from "@/pages/SettingsPage/components/notificationSchedule/utils";
import { EHttpStatus, ESettingSocketTopicID, ESocketTopic } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function NotificationSchedulePage() {
    const [t] = useTranslation();
    const { setPageAliasRef } = usePageHeader();
    const navigate = usePageNavigateRef();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const [selectedRuleUIDs, setSelectedRuleUIDs] = useState<string[]>([]);
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canCreate = hasRoleAction(SettingRole.EAction.NotificationScheduleCreate);
    const canUpdate = hasRoleAction(SettingRole.EAction.NotificationScheduleUpdate);
    const canDelete = hasRoleAction(SettingRole.EAction.NotificationScheduleDelete);
    const { mutateAsync: getNotificationScheduleMutateAsync } = useGetNotificationSchedule();
    const { mutateAsync: createNotificationScheduleRuleMutateAsync } = useCreateNotificationScheduleRule({ interceptToast: true });
    const { mutate: deleteSelectedNotificationScheduleRulesMutate } = useDeleteSelectedNotificationScheduleRules();
    const [ruleSchema, setRuleSchema] = useState<NotificationScheduleRuleModel.IRuleSchema | null>(null);

    useEffect(() => {
        setPageAliasRef.current("Notification schedule");
        getNotificationScheduleMutateAsync({}).then((res) => {
            setRuleSchema(res.notification_rule_schema);
        });

        return () => {
            unsubscribeModelSocketTopic(ESocketTopic.AppSettings, [ESettingSocketTopicID.NotificationSchedule]);
        };
    }, []);

    const createRule = () => {
        if (isValidating || !canCreate || !ruleSchema) {
            return;
        }

        const targetSchema = ruleSchema.targets[0];
        const fieldSchema = targetSchema?.fields[0];
        const rule: ICreateNotificationScheduleRuleForm = {
            name: t("settings.New notification rule"),
            is_enabled: false,
            interval_str: "0 9 * * *",
            timezone: getNotificationScheduleTimezone(),
            target: targetSchema?.key || "card",
            field: fieldSchema?.key || "deadline_at",
            operator: fieldSchema?.operators[0] || "within_next_days",
            value: 3,
            recipients: targetSchema?.recipients.slice(0, 1) || ["card_assignees"],
            repeat_after_hours: 24,
        };

        setIsValidating(true);
        const promise = createNotificationScheduleRuleMutateAsync(rule);

        Toast.Add.promise(promise, {
            loading: t("common.Saving..."),
            error: () => t("errors.Internal server error"),
            success: () => t("successes.Notification schedule saved successfully."),
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const deleteSelectedRules = () => {
        if (isValidating || !selectedRuleUIDs.length || !canDelete) {
            return;
        }

        setIsValidating(true);
        deleteSelectedNotificationScheduleRulesMutate(
            {
                rule_uids: selectedRuleUIDs,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected notification schedule rules deleted successfully."));
                    setSelectedRuleUIDs([]);
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
            <Flex
                justify={{ sm: "between" }}
                direction={{ initial: "col", sm: "row" }}
                gap="2"
                mb="4"
                pb="2"
                textSize="3xl"
                weight="semibold"
                className="scroll-m-20 tracking-tight"
            >
                <span>{t("settings.Notification schedule")}</span>
                <Flex gap="2" wrap justify="end">
                    {selectedRuleUIDs.length > 0 && canDelete && (
                        <Button variant="destructive" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={deleteSelectedRules}>
                            <IconComponent icon="trash" size="4" />
                            {t("common.Delete")}
                        </Button>
                    )}
                    {canCreate && (
                        <Button variant="outline" disabled={isValidating || !ruleSchema} className="gap-2 pl-2 pr-3" onClick={createRule}>
                            <IconComponent icon="plus" size="4" />
                            {t("settings.Add rule")}
                        </Button>
                    )}
                </Flex>
            </Flex>
            <NotificationScheduleForm
                canUpdate={canUpdate}
                canDelete={canDelete}
                isValidating={isValidating}
                ruleSchema={ruleSchema}
                selectedRuleUIDs={selectedRuleUIDs}
                setSelectedRuleUIDs={setSelectedRuleUIDs}
            />
        </>
    );
}

export default NotificationSchedulePage;
