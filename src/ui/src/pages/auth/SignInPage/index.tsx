import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router";
import { QUERY_NAMES, SIGN_IN_TOKEN_LENGTH } from "@/constants";
import { FormOnlyLayout, createTwoSidedSizeClassNames } from "@/components/Layout";
import useAuthEmail from "@/controllers/api/auth/useAuthEmail";
import useGetAuthProvider from "@/controllers/api/auth/useGetAuthProvider";
import useOidcLogin from "@/controllers/api/auth/useOidcLogin";
import { ROUTES } from "@/core/routing/constants";
import { Utils } from "@langboard/core/utils";
import EmailForm from "@/pages/auth/SignInPage/EmailForm";
import PasswordForm from "@/pages/auth/SignInPage/PasswordForm";
import Flex from "@/components/base/Flex";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";

function SignInPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const [email, setEmail] = useState("");
    const [form, setForm] = useState<React.JSX.Element>();
    const { mutate } = useAuthEmail();
    const { data: authProviderData, isFetching: isAuthProviderFetching } = useGetAuthProvider();
    const { mutateAsync: oidcLoginMutateAsync, isPending: isOidcSignInPending } = useOidcLogin({ interceptToast: true });
    const authProvider = authProviderData?.provider ?? "local";

    const { wrapper: wrapperClassName, width: widthClassName } = createTwoSidedSizeClassNames("sm");

    const onOidcSignIn = useCallback(async () => {
        const searchParams = new URLSearchParams(location.search);
        const redirectUrl = searchParams.get(QUERY_NAMES.REDIRECT);
        let parsedRedirectUrl = redirectUrl ?? undefined;
        if (redirectUrl) {
            try {
                parsedRedirectUrl = decodeURIComponent(redirectUrl);
            } catch {
                parsedRedirectUrl = redirectUrl;
            }
        }

        try {
            const data = await oidcLoginMutateAsync({
                redirect: parsedRedirectUrl,
            });
            if (!data.authorize_url) {
                throw new Error();
            }

            window.location.assign(data.authorize_url);
        } catch {
            navigate(`${ROUTES.SIGN_IN.EMAIL}?${searchParams.toString()}`, { replace: true, smooth: true });
        }
    }, [location.search, oidcLoginMutateAsync]);

    useEffect(() => {
        setPageAliasRef.current("Sign In");
        if (isAuthProviderFetching || authProvider === "oidc") {
            return;
        }

        const searchParams = new URLSearchParams(location.search);
        const signTokenParam = searchParams.get(QUERY_NAMES.SIGN_IN_TOKEN);
        const emailTokenParam = searchParams.get(QUERY_NAMES.EMAIL_TOKEN);

        if (!signTokenParam || signTokenParam.length !== SIGN_IN_TOKEN_LENGTH) {
            const token = Utils.String.Token.generate(SIGN_IN_TOKEN_LENGTH);

            searchParams.set(QUERY_NAMES.SIGN_IN_TOKEN, token);

            navigate(`${ROUTES.SIGN_IN.EMAIL}?${searchParams.toString()}`, {
                replace: true,
                smooth: true,
            });

            return;
        }

        if (signTokenParam && emailTokenParam) {
            if (location.pathname !== ROUTES.SIGN_IN.PASSWORD) {
                navigate(`${ROUTES.SIGN_IN.PASSWORD}?${searchParams.toString()}`, { replace: true, smooth: true });
                return;
            }

            mutate(
                { is_token: true, token: emailTokenParam, sign_token: signTokenParam },
                {
                    onSuccess: (data) => {
                        if (!data.token) {
                            throw new Error();
                        }

                        if (email !== data.email) {
                            setEmail(data.email);
                        }
                    },
                    onError: () => {
                        searchParams.delete(QUERY_NAMES.EMAIL_TOKEN);
                        navigate(`${ROUTES.SIGN_IN.EMAIL}?${searchParams.toString()}`, { smooth: true });
                    },
                }
            );
        }
    }, [authProvider, email, isAuthProviderFetching, location, mutate]);

    useEffect(() => {
        if (isAuthProviderFetching) {
            return;
        }

        const searchParams = new URLSearchParams(location.search);
        const signTokenParam = searchParams.get(QUERY_NAMES.SIGN_IN_TOKEN) ?? "";
        const emailTokenParam = searchParams.get(QUERY_NAMES.EMAIL_TOKEN);

        if (authProvider === "oidc") {
            setEmail("");
            setForm(
                <EmailForm
                    signToken=""
                    setEmail={setEmail}
                    className={widthClassName}
                    authProvider={authProvider}
                    onOidcSignIn={onOidcSignIn}
                    isOidcSignInPending={isOidcSignInPending}
                />
            );
            return;
        }

        if (signTokenParam && emailTokenParam) {
            setForm(
                <PasswordForm signToken={signTokenParam} emailToken={emailTokenParam} email={email} setEmail={setEmail} className={widthClassName} />
            );
        } else {
            setEmail("");
            setForm(
                <EmailForm
                    signToken={signTokenParam}
                    setEmail={setEmail}
                    className={widthClassName}
                    authProvider={authProvider}
                    onOidcSignIn={onOidcSignIn}
                    isOidcSignInPending={isOidcSignInPending}
                />
            );
        }
    }, [authProvider, email, isAuthProviderFetching, isOidcSignInPending, location, onOidcSignIn, widthClassName]);

    return (
        <FormOnlyLayout size="default" useLogo>
            <Flex className={wrapperClassName}>{form}</Flex>
        </FormOnlyLayout>
    );
}

export default SignInPage;
