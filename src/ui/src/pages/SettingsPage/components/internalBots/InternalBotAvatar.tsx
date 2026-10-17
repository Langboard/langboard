import AvatarUploader from "@/components/AvatarUploader";
import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import useUpdateInternalBot from "@/controllers/api/settings/internalBots/useUpdateInternalBot";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const InternalBotAvatar = memo(() => {
    const [t] = useTranslation();
    const { model: internalBot } = ModelRegistry.InternalBotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser?.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateInternalBot = hasRoleAction(SettingRole.EAction.InternalBotUpdate);
    const avatar = internalBot.useField("avatar");
    const dataTransferRef = useRef<DataTransfer>(new DataTransfer());
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useUpdateInternalBot(internalBot, { interceptToast: true });

    const onChange = useCallback(
        (files: File[] | FileList) => {
            if (isValidating || !files.length || !canUpdateInternalBot) {
                return;
            }

            setIsValidating(true);

            const file = files[0];
            const hasAvatar = !!avatar;

            const promise = mutateAsync({
                avatar: file,
            });

            showToast(promise, () => {
                if (hasAvatar) {
                    return t("successes.Internal bot avatar changed successfully.");
                } else {
                    return t("successes.Internal bot avatar uploaded successfully.");
                }
            });
        },
        [isValidating]
    );

    const onDeleted = () => {
        if (isValidating || !canUpdateInternalBot) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            delete_avatar: true,
        });

        showToast(promise, () => {
            return t("successes.Internal bot avatar deleted successfully.");
        });
    };

    const showToast = (promise: Promise<unknown>, onSuccess: () => string) => {
        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: onSuccess,
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Flex items="center" direction="col" gap="2">
            <AvatarUploader
                isBot
                notInForm
                initialAvatarUrl={avatar}
                dataTransferRef={dataTransferRef}
                isValidating={isValidating}
                disabled={!canUpdateInternalBot}
                avatarSize={{
                    initial: "lg",
                    md: "2xl",
                }}
                rootClassName="max-w-screen-xs"
                onChange={onChange}
                onDeleted={onDeleted}
            />
        </Flex>
    );
});

export default InternalBotAvatar;
