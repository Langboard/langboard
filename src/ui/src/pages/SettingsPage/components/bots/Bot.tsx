import Avatar from "@/components/base/Avatar";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import PillList from "@/components/base/PillList";
import Popover from "@/components/base/Popover";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useCopyBot from "@/controllers/api/settings/bots/useCopyBot";
import useDeleteBot from "@/controllers/api/settings/bots/useDeleteBot";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { BotModel } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

export interface IBotProps {
    bot: BotModel.TModel;
}

const Bot = memo(({ bot }: IBotProps) => {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canCopyBot = hasRoleAction(SettingRole.EAction.BotCreate);
    const canDeleteBot = hasRoleAction(SettingRole.EAction.BotDelete);
    const { isValidating, setIsValidating } = useAppSetting();
    const { mutateAsync: copyBotMutateAsync } = useCopyBot(bot, { interceptToast: true });
    const { mutateAsync: deleteBotMutateAsync } = useDeleteBot(bot, { interceptToast: true });
    const [isOpened, setIsOpened] = useState(false);
    const name = bot.useField("name");
    const uname = bot.useField("bot_uname");
    const avatar = bot.useField("avatar");

    const copyBot = (e: React.MouseEvent<HTMLButtonElement> | React.PointerEvent<HTMLButtonElement>) => {
        e.preventDefault();
        e.stopPropagation();

        if (isValidating || !canCopyBot) {
            return;
        }

        setIsValidating(true);

        const promise = copyBotMutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Creating..."),
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
                return t("successes.Bot copied successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const deleteBot = (e: React.MouseEvent<HTMLButtonElement>) => {
        e.preventDefault();
        e.stopPropagation();

        if (isValidating || !canDeleteBot) {
            return;
        }

        setIsValidating(true);

        const promise = deleteBotMutateAsync({});

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
                return t("successes.Bot deleted successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const toBotDetails = () => {
        navigate(ROUTES.SETTINGS.BOT_DETAILS(bot.uid), { smooth: true });
    };

    const changeOpenState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        setIsOpened(opened);
    };

    return (
        <PillList.ItemRoot size="sm" className="cursor-pointer" onClick={toBotDetails}>
            <PillList.ItemTitle>
                <Avatar.Root>
                    <Avatar.Image src={avatar} />
                    <Avatar.Fallback>
                        <IconComponent icon="bot" className="size-2/3" />
                    </Avatar.Fallback>
                </Avatar.Root>
                <Box w="full">
                    <Box>{name}</Box>
                    <Box w="full" textSize="xs" className="text-muted-foreground/70">
                        @{uname}
                    </Box>
                </Box>
            </PillList.ItemTitle>
            <PillList.ItemContent
                onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                }}
            >
                <Flex items="center" justify="end" gap="1">
                    {canCopyBot ? (
                        <Button
                            variant="outline"
                            size="icon-sm"
                            title={t("common.Copy")}
                            titleSide="bottom"
                            disabled={isValidating}
                            onPointerDown={copyBot}
                        >
                            <IconComponent icon="copy" size="4" />
                        </Button>
                    ) : null}
                    {canDeleteBot ? (
                        <Popover.Root open={isOpened} onOpenChange={changeOpenState}>
                            <Popover.Trigger asChild>
                                <Button variant="destructive" size="icon-sm" title={t("common.Delete")} titleSide="bottom" disabled={isValidating}>
                                    <IconComponent icon="trash-2" size="5" />
                                </Button>
                            </Popover.Trigger>
                            <Popover.Content align="end">
                                <Box mb="1" textSize={{ initial: "sm", sm: "base" }} weight="semibold" className="text-center">
                                    {t("ask.Are you sure you want to delete this bot?")}
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
                                    <SubmitButton type="button" variant="destructive" size="sm" onClick={deleteBot} isValidating={isValidating}>
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
});

export default Bot;
