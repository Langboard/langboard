import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import Collaborative from "@/components/Collaborative";
import useUpdateBot from "@/controllers/api/settings/bots/useUpdateBot";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const BotApiURL = memo(() => {
    const [t] = useTranslation();
    const { model: bot } = ModelRegistry.BotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateBot = hasRoleAction(SettingRole.EAction.BotUpdate);
    const apiURL = bot.useField("api_url");
    const inputID = useId();
    const { mutateAsync } = useUpdateBot(bot, { interceptToast: true });
    const inputRef = useRef<HTMLInputElement>(null);
    const [isValidating, setIsValidating] = useState(false);

    const change = () => {
        if (isValidating || !inputRef.current || !canUpdateBot) {
            return;
        }

        const value = inputRef.current.value.trim();
        if (value === apiURL || !value || !Utils.String.isValidURL(value)) {
            inputRef.current.value = apiURL;
            return;
        }

        const promise = mutateAsync({
            api_url: value,
        });

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
                return t("successes.Bot API URL changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            change();
            return;
        }
    };

    return (
        <Flex items="center" gap="1">
            <Collaborative.Input
                id={inputID}
                className="peer"
                collaborationType={EEditorCollaborationType.AppSettings}
                uid={bot.uid}
                section="bot"
                field="api_url"
                placeholder=" "
                autoComplete="off"
                defaultValue={apiURL}
                onKeyDown={handleKeyDown}
                disabled={!canUpdateBot}
                ref={inputRef}
            >
                <Floating.Label className="select-none" htmlFor={inputID}>
                    {t("settings.Bot API URL")}
                </Floating.Label>
            </Collaborative.Input>
            <Button type="button" size="icon-sm" variant="ghost" onClick={change} disabled={!canUpdateBot || isValidating} title={t("common.Save")}>
                <IconComponent icon="check" size="4" />
            </Button>
        </Flex>
    );
});

export default BotApiURL;
