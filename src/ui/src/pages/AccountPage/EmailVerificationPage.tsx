import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { QUERY_NAMES } from "@/constants";
import Toast from "@/components/base/Toast";
import useVerifyNewEmail from "@/controllers/api/account/useVerifyNewEmail";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { useAuth } from "@/core/providers/AuthProvider";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { EHttpStatus } from "@langboard/core/enums";

function EmailVerificationPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const { updatedUser } = useAuth();
    const location = useLocation();
    const navigate = usePageNavigateRef();
    const { mutate } = useVerifyNewEmail();

    useEffect(() => {
        setPageAliasRef.current(t("myAccount.Email verification"));
    }, [setPageAliasRef, t]);

    useEffect(() => {
        const searchParams = new URLSearchParams(location.search);
        const token = searchParams.get(QUERY_NAMES.SUB_EMAIL_VERIFY_TOKEN);

        if (!token) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        mutate(
            { verify_token: token },
            {
                onSuccess: () => {
                    updatedUser();
                    Toast.Add.success(t("successes.The email verified successfully."));
                    navigate(ROUTES.ACCOUNT.EMAILS.ROUTE);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_304_NOT_MODIFIED]: {
                            after: () => navigate(ROUTES.ACCOUNT.EMAILS.ROUTE),
                        },
                        [EHttpStatus.HTTP_404_NOT_FOUND]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)),
                        },
                        [EHttpStatus.HTTP_409_CONFLICT]: {
                            after: () => navigate(ROUTES.ACCOUNT.EMAILS.ROUTE),
                        },
                    });

                    handle(error);
                },
            }
        );
    }, []);

    return <></>;
}

export default EmailVerificationPage;
