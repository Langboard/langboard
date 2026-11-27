import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Popover from "@/components/base/Popover";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import { useBotScheduleList } from "@/components/bots/BotScheduleList/Provider";
import useUnscheduleBotCron from "@/controllers/api/shared/botSchedules/useUnscheduleBotCron";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { BaseBotScheduleModel } from "@/core/models";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export interface IBotScheduleListItemDeleteProps {
    schedule: BaseBotScheduleModel.TModel;
    variant?: React.ComponentProps<typeof Button>["variant"];
    className?: string;
}

function BotScheduleListItemDelete({
    schedule,
    variant = "outline",
    className = "border-0 [&:first-child]:rounded-b-none [&:not(:first-child)]:rounded-t-none [&:not(:first-child)]:border-t",
}: IBotScheduleListItemDeleteProps): React.JSX.Element {
    const { bot, params, target } = useBotScheduleList();
    const [t] = useTranslation();
    const [isValidating, setIsValidating] = useState(false);
    const projectUID = "project_uid" in target ? target.project_uid : target.uid;
    const { mutateAsync: unscheduleBotCronMutateAsync } = useUnscheduleBotCron(
        { ...params, bot_uid: bot.uid, project_uid: projectUID, schedule_uid: schedule.uid, target_uid: target.uid },
        { interceptToast: true }
    );
    const [isOpened, setIsOpened] = useState(false);

    const unschedule = () => {
        if (isValidating) {
            return;
        }

        setIsValidating(true);

        const promise = unscheduleBotCronMutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Deleting..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Bot unscheduled successfully.");
            },
            finally: () => {
                setIsValidating(false);
                setIsOpened(false);
            },
        });
    };

    return (
        <Popover.Root modal open={isOpened} onOpenChange={setIsOpened}>
            <Popover.Trigger asChild>
                <Button size="sm" variant={variant} disabled={isValidating} className={className}>
                    {t("bot.schedules.Unschedule")}
                </Button>
            </Popover.Trigger>
            <Popover.Content className="w-auto min-w-0 max-w-xs sm:max-w-sm md:max-w-md lg:max-w-lg">
                <Box mb="1" textSize={{ initial: "sm", sm: "base" }} weight="semibold" className="text-center">
                    {t("ask.Are you sure you want to unschedule this cron?")}
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
                    <SubmitButton type="button" size="sm" variant="destructive" onClick={unschedule} isValidating={isValidating}>
                        {t("bot.schedules.Unschedule")}
                    </SubmitButton>
                </Flex>
            </Popover.Content>
        </Popover.Root>
    );
}

export default BotScheduleListItemDelete;
