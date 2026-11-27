import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import { NotificationScheduleRuleModel } from "@/core/models";
import NotificationRuleEditor from "@/pages/SettingsPage/components/notificationSchedule/NotificationRuleEditor";
import { Utils } from "@langboard/core/utils";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export interface INotificationScheduleFormProps {
    canUpdate: bool;
    canDelete: bool;
    isValidating: bool;
    ruleSchema: NotificationScheduleRuleModel.IRuleSchema | null;
    selectedRuleUIDs: string[];
    setSelectedRuleUIDs: React.Dispatch<React.SetStateAction<string[]>>;
}

function NotificationScheduleForm({
    canUpdate,
    canDelete,
    isValidating,
    ruleSchema,
    selectedRuleUIDs,
    setSelectedRuleUIDs,
}: INotificationScheduleFormProps) {
    const [t] = useTranslation();
    const rules = NotificationScheduleRuleModel.Model.useModels(() => true);
    const [expandedRuleUid, setExpandedRuleUid] = useState<string | null>(null);
    const lastRunAt = rules
        .map((rule) => rule.last_run_at)
        .filter((value): value is Date => !!value)
        .sort((a, b) => b.getTime() - a.getTime())[0];

    useEffect(() => {
        const ruleUIDs = new Set(rules.map((rule) => rule.uid));
        setSelectedRuleUIDs((prev) => {
            const next = prev.filter((uid) => ruleUIDs.has(uid));
            return next.length === prev.length ? prev : next;
        });
    }, [rules]);

    const toggleRuleSelection = (uid: string) => {
        setSelectedRuleUIDs((prev) => {
            if (prev.includes(uid)) {
                return prev.filter((selectedUID) => selectedUID !== uid);
            }

            return [...prev, uid];
        });
    };

    return (
        <Box className="space-y-6">
            <Flex items="center">
                <div className="text-sm font-medium">{t("settings.Notification rules")}</div>
            </Flex>
            <Box className="space-y-3">
                {rules.map((rule) => (
                    <NotificationRuleEditor
                        key={rule.uid}
                        rule={rule}
                        schema={ruleSchema}
                        disabled={!canUpdate || isValidating}
                        isExpanded={expandedRuleUid === rule.uid}
                        isSelected={selectedRuleUIDs.includes(rule.uid)}
                        canDelete={canDelete}
                        onSelect={() => toggleRuleSelection(rule.uid)}
                        onToggle={() => setExpandedRuleUid((prev) => (prev === rule.uid ? null : rule.uid))}
                    />
                ))}
            </Box>
            <Box textSize="sm" className="text-muted-foreground">
                {t("settings.Last run")}: {lastRunAt ? Utils.String.formatDateLocale(lastRunAt) : t("settings.Never")}
            </Box>
        </Box>
    );
}

export default NotificationScheduleForm;
