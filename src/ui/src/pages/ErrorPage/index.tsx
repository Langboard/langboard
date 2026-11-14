import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Separator from "@/components/base/Separator";
import { useAuth } from "@/core/providers/AuthProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import getErrorMessage from "@/pages/ErrorPage/getErrorMessage";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";

function ErrorPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const { currentUser } = useAuth();
    const navigate = usePageNavigateRef();
    const code = window.location.pathname.split("/").pop();
    let errorCode = EHttpStatus[code as keyof typeof EHttpStatus];
    if (!errorCode) {
        errorCode = EHttpStatus.HTTP_404_NOT_FOUND;
    }

    if (!Utils.Type.isNumber(errorCode)) {
        errorCode = EHttpStatus[errorCode as keyof typeof EHttpStatus];
    }

    const message = getErrorMessage(errorCode);

    useEffect(() => {
        setPageAliasRef.current(message);
    }, []);

    const handleBack = () => {
        if (currentUser) {
            navigate(ROUTES.DASHBOARD.PROJECTS.ALL);
        } else {
            navigate(ROUTES.SIGN_IN.EMAIL);
        }
    };

    return (
        <Flex direction="col" items="center" justify="center" minH="screen" gap="4" className="px-6 text-center">
            <h1 className="flex flex-wrap items-center justify-center gap-3 text-2xl font-medium text-foreground sm:text-3xl">
                {errorCode.toString()}
                <Separator className="mt-1 h-8 w-0.5" orientation="vertical" />
                {message}
            </h1>
            <Button onClick={handleBack}>{t(currentUser ? "common.Go to Dashboard" : "common.Go to Sign In")}</Button>
        </Flex>
    );
}

export default ErrorPage;
