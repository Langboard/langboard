import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { QUERY_NAMES } from "@/constants";
import { FormOnlyLayout } from "@/components/Layout";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import useActivateUser from "@/controllers/api/auth/useActivateUser";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ROUTES } from "@/core/routing/constants";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EHttpStatus } from "@langboard/core/enums";

function ActivatePage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const { mutate } = useActivateUser();
    const [description, setDescription] = useState<React.JSX.Element | null>(null);
    const [isLoaded, setIsLoaded] = useState(false);

    useEffect(() => {
        setPageAliasRef.current(t("auth.Activate account"));
    }, [setPageAliasRef, t]);

    useEffect(() => {
        if (!isLoaded) {
            setIsLoaded(true);
            return;
        }

        const searchParams = new URLSearchParams(location.search);
        const token = searchParams.get(QUERY_NAMES.SIGN_UP_ACTIVATE_TOKEN);

        if (!token) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        mutate(
            { signup_token: token },
            {
                onSuccess: () => {
                    setDescription(
                        <>
                            <h2 className="text-center text-2xl font-normal xs:text-3xl">{t("auth.activate.Welcome to {app}!")}</h2>
                            <p className="mt-8 text-sm xs:text-base">
                                {t("auth.activate.Your account is now active and ready to go!")}&nbsp;
                                {t("auth.activate.Dive in and discover all the amazing features we have in store for you.")}
                            </p>
                            <p className="mt-4 text-sm xs:text-base">
                                {t("auth.activate.Simply sign in with your email and password to get started.")}
                            </p>
                            <Flex justify="center" mt="8">
                                <Button onClick={() => navigate(ROUTES.SIGN_IN.EMAIL, { smooth: true })}>{t("auth.Sign in")}</Button>
                            </Flex>
                        </>
                    );
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_404_NOT_FOUND]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { smooth: true }),
                        },
                        [EHttpStatus.HTTP_409_CONFLICT]: {
                            after: () => navigate(ROUTES.SIGN_IN.EMAIL, { smooth: true }),
                        },
                    });

                    handle(error);
                },
            }
        );
    }, [isLoaded]);

    return (
        <FormOnlyLayout size="sm" useLogo>
            {description}
        </FormOnlyLayout>
    );
}

export default ActivatePage;
