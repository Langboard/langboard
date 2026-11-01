import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Card from "@/components/base/Card";
import Flex from "@/components/base/Flex";
import BotScheduleListItemDelete from "@/components/bots/BotScheduleList/ItemDelete";
import BotScheduleListItemEdit from "@/components/bots/BotScheduleList/ItemEdit";
import BotScheduleAdmission from "@/components/bots/BotScheduleList/Admission";
import { useBotScheduleList } from "@/components/bots/BotScheduleList/Provider";
import { CronText } from "@/components/Cron";
import { BaseBotScheduleModel } from "@/core/models";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

export interface IBotScheduleListItemProps {
    schedule: BaseBotScheduleModel.TModel;
}

function BotScheduleListItem({ schedule }: IBotScheduleListItemProps): React.JSX.Element {
    const { setCopiedForm, setIsAddMode } = useBotScheduleList();
    const [t] = useTranslation();
    const runningType = schedule.useField("running_type");
    const status = schedule.useField("status");
    const admission = schedule.useField("last_admission");
    const startAt = schedule.useField("start_at");
    const endAt = schedule.useField("end_at");
    const rawIntervalStr = schedule.useField("interval_str");
    const intervalStr = useMemo(() => Utils.String.Crontab.restoreTimezone(rawIntervalStr), [rawIntervalStr]);
    const copy = useCallback(() => {
        setCopiedForm(() => ({
            runningType: schedule.running_type,
            interval: schedule.interval_str,
            startAt: schedule.start_at,
            endAt: schedule.end_at,
        }));

        setIsAddMode(true);
    }, [setIsAddMode, setCopiedForm]);

    return (
        <Card.Root className="flex flex-col pt-6">
            <Card.Content className="flex-1 text-left">
                <Flex direction="col" gap="1.5">
                    <BotScheduleListItemSection title={t("bot.schedules.cronHeaders.Interval")}>
                        <CronText value={intervalStr} className="gap-y-0.5" />
                    </BotScheduleListItemSection>
                    <BotScheduleListItemSection title={t("bot.schedules.cronHeaders.Type")}>
                        {t(`bot.schedules.cronRunningTypes.${runningType}`)}
                    </BotScheduleListItemSection>
                    <BotScheduleListItemSection title={t("bot.schedules.cronHeaders.Status")}>
                        <Box
                            className={cn(
                                status === BaseBotScheduleModel.EStatus.Pending && "text-yellow-500",
                                status === BaseBotScheduleModel.EStatus.Started && "text-green-500",
                                status === BaseBotScheduleModel.EStatus.Stopped && "text-red-500"
                            )}
                        >
                            {t(`bot.schedules.cronStatus.${status}`)}
                        </Box>
                    </BotScheduleListItemSection>
                    {BaseBotScheduleModel.RUNNING_TYPES_WITH_START_AT.includes(runningType) && !!startAt && (
                        <BotScheduleListItemSection
                            title={t(
                                `bot.schedules.cronHeaders.${runningType === BaseBotScheduleModel.ERunningType.Onetime ? "Execute at" : "Start at"}`
                            )}
                        >
                            {Utils.String.formatDateLocale(startAt)}
                        </BotScheduleListItemSection>
                    )}
                    {BaseBotScheduleModel.RUNNING_TYPES_WITH_END_AT.includes(runningType) && !!endAt && (
                        <BotScheduleListItemSection title={t("bot.schedules.cronHeaders.End at")}>
                            {Utils.String.formatDateLocale(endAt)}
                        </BotScheduleListItemSection>
                    )}
                </Flex>
                <BotScheduleAdmission admission={admission} />
            </Card.Content>
            <Card.Footer className="justify-end gap-2">
                <BotScheduleListItemDelete schedule={schedule} className="" variant="secondary" />
                <Button variant="secondary" size="sm" onClick={copy}>
                    {t("common.Copy")}
                </Button>
                <BotScheduleListItemEdit schedule={schedule} className="" variant="default" />
            </Card.Footer>
        </Card.Root>
    );
}

interface IBotScheduleListItemSectionProps {
    title: string;
    children: React.ReactNode;
}

function BotScheduleListItemSection({ title, children }: IBotScheduleListItemSectionProps): React.JSX.Element {
    return (
        <Flex items="start" gap="1.5">
            <Box className="w-20 text-gray-500">{title}</Box>
            <Box weight="semibold">{children}</Box>
        </Flex>
    );
}

export default BotScheduleListItem;
