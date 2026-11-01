import { useTranslation } from "react-i18next";
import Toast from "@/components/base/Toast";
import useUpdatePreferredLanguage from "@/controllers/api/account/useUpdatePreferredLanguage";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { AuthUser } from "@/core/models";
import { useState } from "react";
import { normalizeLocale } from "@/core/utils/LocalePolicy";
import { useUserAvatar } from "@/components/UserAvatar/Provider";
import { useIsMutating, useQueryClient } from "@tanstack/react-query";

export interface IUserPreferenceLanguageSwitcherProps extends Omit<React.ComponentProps<typeof LanguageSwitcher>, "asForm"> {
    currentUser: AuthUser.TModel;
}

function UserPreferenceLanguageSwitcher({ currentUser, ...props }: IUserPreferenceLanguageSwitcherProps): React.JSX.Element {
    const [isValidating, setIsValidating] = useState(false);
    const [t, i18n] = useTranslation();
    const { mutateAsync } = useUpdatePreferredLanguage({ interceptToast: true });
    const queryClient = useQueryClient();
    const languageWrites = useIsMutating({ mutationKey: ["update-preferred-language"] });
    const { getAvatarHoverCardAttrs, setIsHoverLocked } = useUserAvatar();

    const handleUpdate = (lang: string) => {
        if (isValidating || queryClient.isMutating({ mutationKey: ["update-preferred-language"] })) {
            return;
        }

        const previousLanguage = normalizeLocale(i18n.language);
        setIsValidating(true);

        const promise = i18n.changeLanguage(lang).then(() => mutateAsync({ lang }));

        Toast.Add.promise(promise, {
            loading: t("common.Updating..."),
            error: (error) => {
                i18n.changeLanguage(previousLanguage);
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                currentUser.preferred_lang = lang;
                return t("successes.Preferred language updated successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <LanguageSwitcher
            {...props}
            contentAttrs={getAvatarHoverCardAttrs()}
            onOpenChange={setIsHoverLocked}
            asForm={{
                initialValue: normalizeLocale(i18n.language),
                disabled: isValidating || languageWrites > 0,
                includeCurrent: true,
                onChange: handleUpdate,
            }}
        />
    );
}

export default UserPreferenceLanguageSwitcher;
