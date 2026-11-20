import { useTranslation } from "react-i18next";
import FormErrorMessage from "@/components/FormErrorMessage";
import { QUERY_NAMES } from "@/constants";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import Form from "@/components/base/Form";
import SubmitButton from "@/components/base/SubmitButton";
import useAuthEmail from "@/controllers/api/auth/useAuthEmail";
import { TAuthProvider } from "@/controllers/api/auth/useGetAuthProvider";
import useForm from "@/core/hooks/form/useForm";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EHttpStatus } from "@langboard/core/enums";

export interface IEmailFormProps {
    signToken: string;
    setEmail: (email: string) => void;
    className: string;
    authProvider: TAuthProvider;
    onOidcSignIn: () => void;
    isOidcSignInPending: bool;
}

function EmailForm({ signToken, setEmail, className, authProvider, onOidcSignIn, isOidcSignInPending }: IEmailFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { mutate } = useAuthEmail();
    const isOidcOnly = authProvider === "oidc";
    const isHybrid = authProvider === "hybrid";
    const { errors, setErrors, isValidating, handleSubmit, formRef } = useForm({
        errorLangPrefix: "auth.errors",
        schema: {
            email: { required: true, email: true },
        },
        predefineValues: { is_token: false, sign_token: signToken },
        mutate,
        mutateOnSuccess: (data) => {
            if (!data.token) {
                setErrors({ email: "errors.requests.VA1001" });
                return;
            }

            setEmail(formRef.current!.email.value);

            const searchParams = new URLSearchParams(location.search);
            searchParams.append(QUERY_NAMES.SIGN_IN_TOKEN, signToken);
            searchParams.append(QUERY_NAMES.EMAIL_TOKEN, data.token);

            navigate(`${ROUTES.SIGN_IN.PASSWORD}?${searchParams.toString()}`, { smooth: true });
        },
        apiErrorHandlers: {
            [EHttpStatus.HTTP_404_NOT_FOUND]: {
                after: (message) => {
                    setErrors({ email: message as string });
                    setTimeout(() => {
                        formRef.current!.email.focus();
                    }, 0);
                },
                toast: false,
            },
            [EHttpStatus.HTTP_406_NOT_ACCEPTABLE]: {
                after: (message) => {
                    setErrors({ email: message as string });
                },
                toast: false,
            },
        },
        useDefaultBadRequestHandler: true,
    });

    return (
        <>
            <Box className={className}>
                <h2 className="text-4xl font-normal">{t("auth.Sign in")}</h2>
                <Box mt="4" textSize="base">
                    {t("auth.Use your {app} Account")}
                </Box>
            </Box>
            {isOidcOnly ? (
                <Flex className={cn("mt-11 xs:mt-0", className)} items="center" justify="end">
                    <Button type="button" onClick={onOidcSignIn} disabled={isOidcSignInPending}>
                        {t("auth.Continue with SSO")}
                    </Button>
                </Flex>
            ) : (
                <Form.Root className={cn("mt-11 xs:mt-0", className)} onSubmit={handleSubmit} ref={formRef}>
                    <Form.Field name="email">
                        <Floating.LabelInput label={t("user.Email")} isFormControl autoFocus autoComplete="email" disabled={isValidating} />
                        {errors.email && <FormErrorMessage error={errors.email} icon="circle-alert" />}
                    </Form.Field>
                    <Flex items="center" gap="3" justify={{ initial: "between", xs: "end" }} mt="8">
                        <Button
                            type="button"
                            variant="ghost"
                            disabled={isValidating}
                            onClick={() =>
                                navigate(`${ROUTES.SIGN_UP.REQUIRED}?${new URLSearchParams(location.search).toString()}`, { smooth: true })
                            }
                        >
                            {t("auth.Create account")}
                        </Button>
                        <SubmitButton type="submit" isValidating={isValidating}>
                            {t("common.Next")}
                        </SubmitButton>
                    </Flex>
                    {isHybrid && (
                        <Flex justify={{ initial: "between", xs: "end" }} mt="4">
                            <Button type="button" variant="outline" onClick={onOidcSignIn} disabled={isValidating || isOidcSignInPending}>
                                {t("auth.Continue with SSO")}
                            </Button>
                        </Flex>
                    )}
                </Form.Root>
            )}
        </>
    );
}

export default EmailForm;
