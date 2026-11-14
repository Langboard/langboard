import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import FormErrorMessage from "@/components/FormErrorMessage";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import Form from "@/components/base/Form";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useSendResetLink from "@/controllers/api/auth/useSendResetLink";
import useForm from "@/core/hooks/form/useForm";
import SuccessResult from "@/pages/auth/AccountRecoveryPage/SuccessResult";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EHttpStatus } from "@langboard/core/enums";

export interface ISendResetLinkFormProps {
    signToken: string;
    emailToken: string;
    backToSignin: () => void;
}

function SendResetLinkForm({ signToken, emailToken, backToSignin }: ISendResetLinkFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const { mutate } = useSendResetLink();
    const isResendRef = useRef(false);
    const { errors, isValidating, handleSubmit, formRef } = useForm({
        errorLangPrefix: "accountRecovery.errors",
        schema: () => ({
            firstname: { required: !isResendRef.current },
            lastname: { required: !isResendRef.current },
        }),
        beforeHandleSubmit: () => {
            isResendRef.current = !(location.state?.isTwoSidedView ?? true);
        },
        predefineValues: () => {
            if (isResendRef.current) {
                return { sign_token: signToken, email_token: emailToken, is_resend: true };
            } else {
                return { sign_token: signToken, email_token: emailToken };
            }
        },
        mutate,
        mutateOnSuccess: () => {
            if (isResendRef.current) {
                Toast.Add.success(t("accountRecovery.Resent link successfully."));
            } else {
                navigate(location, { state: { isTwoSidedView: false }, smooth: true });
            }
        },
        apiErrorHandlers: {
            [EHttpStatus.HTTP_404_NOT_FOUND]: {
                after: backToSignin,
            },
        },
        useDefaultBadRequestHandler: true,
    });

    if (!(location.state?.isTwoSidedView ?? true)) {
        const buttons = (
            <>
                <Button type="button" onClick={backToSignin}>
                    {t("common.Back to Sign In")}
                </Button>
                <Button type="button" onClick={() => handleSubmit({})}>
                    {t("accountRecovery.Resend Link")}
                </Button>
            </>
        );
        return (
            <SuccessResult title={t("accountRecovery.Password Reset Link Sent")} buttons={buttons}>
                <p>
                    {t("accountRecovery.A password reset link has been successfully sent to your registered email address.")}&nbsp;
                    {t(
                        // eslint-disable-next-line @/max-len
                        "accountRecovery.Please check your inbox (and spam/junk folder) for the email. Follow the instructions in the email to reset your password."
                    )}
                </p>
                <p className="mt-6">
                    {t("accountRecovery.If you do not receive the email within a few minutes, please try again or contact support for assistance.")}
                </p>
            </SuccessResult>
        );
    } else {
        return (
            <Form.Root className="mt-11 xs:mt-0" onSubmit={handleSubmit} ref={formRef}>
                <Form.Field name="firstname">
                    <Floating.LabelInput label={t("user.First Name")} isFormControl autoFocus disabled={isValidating} />
                    {errors.firstname && <FormErrorMessage error={errors.firstname} icon="circle-alert" />}
                </Form.Field>
                <Form.Field name="lastname" className="mt-3">
                    <Floating.LabelInput label={t("user.Last Name")} isFormControl disabled={isValidating} />
                    {errors.lastname && <FormErrorMessage error={errors.lastname} icon="circle-alert" />}
                </Form.Field>
                <Flex items="center" gap="3" justify="end" mt="8">
                    <SubmitButton type="submit" isValidating={isValidating}>
                        {t("common.Next")}
                    </SubmitButton>
                </Flex>
            </Form.Root>
        );
    }
}

export default SendResetLinkForm;
