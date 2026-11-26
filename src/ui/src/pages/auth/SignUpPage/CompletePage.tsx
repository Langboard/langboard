import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { FormOnlyLayout } from "@/components/Layout";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import useResendSignUpLink from "@/controllers/api/auth/useResendSignUpLink";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ROUTES } from "@/core/routing/constants";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EHttpStatus } from "@langboard/core/enums";

function CompletePage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const { mutate } = useResendSignUpLink();
    const [isResending, setIsResending] = useState(false);

    useEffect(() => {
        setPageAliasRef.current(t("auth.Sign up"));
    }, [setPageAliasRef, t]);

    useEffect(() => {
        if (!location.state?.email) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }
    }, []);

    const resend = () => {
        if (!location.state?.email) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        if (isResending) {
            return;
        }

        setIsResending(true);

        mutate(
            { email: location.state.email },
            {
                onSuccess: () => {
                    Toast.Add.success(t("auth.complete.Email has been resent."));
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_404_NOT_FOUND]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)),
                        },
                        [EHttpStatus.HTTP_409_CONFLICT]: {
                            after: () => navigate(ROUTES.SIGN_IN.EMAIL),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsResending(false);
                },
            }
        );
    };

    return (
        <FormOnlyLayout size="sm" useLogo>
            <h2 className="text-center text-2xl font-normal xs:text-3xl">{t("auth.complete.One More Step to Sign Up")}</h2>
            <p className="mt-8 text-sm xs:text-base">
                {t("auth.complete.We have sent you an email to verify your email address.")}&nbsp;
                {t("auth.complete.Please check your inbox and click on the link to complete your sign up.")}
            </p>
            <p className="mt-4 text-sm xs:text-base">{t("auth.complete.If you don't see the email, check junk, spam, social, or other folders.")}</p>
            <Flex items="center" justify="center" gap="3" mt="8">
                <span className="text-sm xs:text-base">{t("auth.complete.Haven't received the email?")}</span>
                <Button type="button" variant="outline" onClick={resend} disabled={isResending}>
                    {t("auth.complete.Resend")}
                </Button>
            </Flex>
        </FormOnlyLayout>
    );
}

export default CompletePage;
