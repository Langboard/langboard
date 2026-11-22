import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { FormOnlyLayout } from "@/components/Layout";
import Button from "@/components/base/Button";
import { ISignUpForm } from "@/controllers/api/auth/useSignUp";
import { ROUTES } from "@/core/routing/constants";
import AdditionalForm from "@/pages/auth/SignUpPage/AdditionalForm";
import OptionalForm from "@/pages/auth/SignUpPage/OptionalForm";
import Overview from "@/pages/auth/SignUpPage/Overview";
import RequiredForm from "@/pages/auth/SignUpPage/RequiredForm";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";

function SignUpPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const location = useLocation();
    const navigate = usePageNavigateRef();
    const [form, setForm] = useState<React.JSX.Element>();
    const initialErrorsRef = useRef<Record<string, string>>({});
    const values = location.state ?? {};

    const moveStep = (newValues: Partial<ISignUpForm>, nextUrl: string, initialErrors?: Record<string, string>) => {
        const searchParams = new URLSearchParams(location.search);

        delete (newValues as Record<string, unknown>)["password-confirm"];
        location.state = { ...location.state, ...newValues };
        if (initialErrors) {
            initialErrorsRef.current = initialErrors;
        }
        navigate(location, { replace: true, state: location.state, smooth: true });
        navigate(`${nextUrl}?${searchParams.toString()}`, { state: location.state, smooth: true });
    };

    const backToSignIn = () => {
        const searchParams = new URLSearchParams(location.search);
        navigate(`${ROUTES.SIGN_IN.EMAIL}?${searchParams.toString()}`, { smooth: true });
    };

    useEffect(() => {
        setPageAliasRef.current(t("auth.Sign up"));
    }, [setPageAliasRef, t]);

    useEffect(() => {
        const searchParams = new URLSearchParams(location.search);

        switch (location.pathname) {
            case ROUTES.SIGN_UP.REQUIRED:
                setForm(<RequiredForm values={values} moveStep={moveStep} initialErrorsRef={initialErrorsRef} />);
                break;
            case ROUTES.SIGN_UP.ADDITIONAL: {
                const inputNames: (keyof ISignUpForm)[] = ["email", "firstname", "lastname", "password"];
                for (let i = 0; i < inputNames.length; ++i) {
                    if (!values[inputNames[i]]) {
                        navigate(`${ROUTES.SIGN_UP.REQUIRED}?${searchParams.toString()}`);
                        return;
                    }
                }
                setForm(<AdditionalForm values={values} moveStep={moveStep} initialErrorsRef={initialErrorsRef} />);
                break;
            }
            case ROUTES.SIGN_UP.OPTIONAL: {
                const inputNames: (keyof ISignUpForm)[] = ["email", "firstname", "lastname", "password", "industry", "purpose"];
                for (let i = 0; i < inputNames.length; ++i) {
                    if (!values[inputNames[i]]) {
                        navigate(`${ROUTES.SIGN_UP.REQUIRED}?${searchParams.toString()}`);
                        return;
                    }
                }
                setForm(<OptionalForm values={values} moveStep={moveStep} initialErrorsRef={initialErrorsRef} />);
                break;
            }
            case ROUTES.SIGN_UP.OVERVIEW: {
                const inputNames: (keyof ISignUpForm)[] = ["email", "firstname", "lastname", "password", "industry", "purpose"];
                for (let i = 0; i < inputNames.length; ++i) {
                    if (!values[inputNames[i]]) {
                        navigate(`${ROUTES.SIGN_UP.REQUIRED}?${searchParams.toString()}`);
                        return;
                    }
                }
                setForm(<Overview values={values} moveStep={moveStep} />);
                break;
            }
        }
    }, [location]);

    const leftSide = (
        <>
            <h2 className="text-4xl font-normal">{t("auth.Sign up")}</h2>
            <span className="text-sm sm:text-base">{t("auth.Already have an account?")}</span>
            <Button
                id="back-to-sign-in-btn"
                type="button"
                variant="ghost"
                size={{
                    initial: "sm",
                    sm: "default",
                }}
                className="ml-2 mt-4"
                onClick={backToSignIn}
            >
                {t("auth.Sign in")}
            </Button>
        </>
    );

    return <FormOnlyLayout size="sm" useLogo leftSide={leftSide} rightSide={form} />;
}

export default SignUpPage;
