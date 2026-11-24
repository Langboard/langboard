import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import CopyInput from "@/components/CopyInput";
import useGenerateNewBotApiToken from "@/controllers/api/settings/bots/useGenerateNewBotApiToken";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const BotAppApiToken = memo(() => {
    const [t] = useTranslation();
    const { model: bot } = ModelRegistry.BotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateBot = hasRoleAction(SettingRole.EAction.BotUpdate);
    const appApiToken = bot.useField("app_api_token");
    const { mutateAsync } = useGenerateNewBotApiToken(bot, { interceptToast: true });
    const [isValidating, setIsValidating] = useState(false);
    const [opened, setOpened] = useState(false);
    const [revealedToken, setRevealedToken] = useState<string>();

    const generate = () => {
        if (isValidating || !canUpdateBot) {
            return;
        }

        const promise = mutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Refreshing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: (data) => {
                setTimeout(() => {
                    bot.app_api_token = data.secret_app_api_token;
                    setRevealedToken(data.revealed_app_api_token);
                    setOpened(true);
                }, 0);
                return t("successes.Bot App API token generated successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const changeOpenedState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        setRevealedToken(undefined);
        setOpened(opened);
    };

    return (
        <>
            <Flex items="center" justify="between" gap="2">
                <Floating.LabelInput
                    label={t("settings.Bot App API token")}
                    autoComplete="off"
                    defaultValue={appApiToken}
                    wrapperProps={{ className: "max-w-[calc(100%_-_theme(spacing.10))]" }}
                    disabled
                />
                <Button size="icon-sm" onClick={generate} title={t("settings.Generate new API token")} disabled={!canUpdateBot}>
                    <IconComponent icon="refresh-ccw" size="4" />
                </Button>
            </Flex>
            <Dialog.Root open={opened} onOpenChange={changeOpenedState}>
                <Dialog.Content className="sm:max-w-md" aria-describedby="">
                    <Dialog.Header>
                        <Dialog.Title>{t("settings.Generated new token")}</Dialog.Title>
                    </Dialog.Header>
                    <CopyInput value={revealedToken} className="mt-4" />
                    <Dialog.Footer className="mt-6 flex-col gap-2 sm:justify-end sm:gap-0">
                        <Dialog.Close asChild>
                            <Button type="button" variant={!revealedToken ? "destructive" : "outline"} disabled={isValidating}>
                                {t(!revealedToken ? "common.Cancel" : "common.Close")}
                            </Button>
                        </Dialog.Close>
                    </Dialog.Footer>
                </Dialog.Content>
            </Dialog.Root>
        </>
    );
});

export default BotAppApiToken;
