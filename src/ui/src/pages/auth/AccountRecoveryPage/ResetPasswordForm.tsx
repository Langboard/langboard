import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import PasswordInput from "@/components/PasswordInput";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import SubmitButton from "@/components/base/SubmitButton";
import useRecoveryPassword from "@/controllers/api/auth/useRecoveryPassword";
import useForm from "@/core/hooks/form/useForm";
import SuccessResult from "@/pages/auth/AccountRecoveryPage/SuccessResult";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { EHttpStatus } from "@langboard/core/enums";

export interface IResetPasswordFormProps {
    recoveryToken: string;
    backToSignin: () => void;
}

function ResetPasswordForm({ recoveryToken, backToSignin }: IResetPasswordFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const location = useLocation();
    const navigate = usePageNavigateRef();
    const { mutate } = useRecoveryPassword();
    const { errors, isValidating, handleSubmit, formRef } = useForm({
        errorLangPrefix: "accountRecovery.errors",
        schema: {
            password: { required: true },
            "password-confirm": { required: true, sameWith: "password" },
        },
        predefineValues: { recovery_token: recoveryToken },
        mutate,
        mutateOnSuccess: () => {
            navigate(location, { state: { isTwoSidedView: false } });
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
            <Button type="button" onClick={backToSignin}>
                {t("common.Back to Sign In")}
            </Button>
        );
        return (
            <SuccessResult title={t("accountRecovery.Password Reset Successful")} buttons={buttons}>
                <p>
                    {t("accountRecovery.Your password has been successfully reset.")}&nbsp;
                    {t("accountRecovery.You can now sign in with your new password.")}
                </p>
            </SuccessResult>
        );
    } else {
        return (
            <Form.Root className="mt-11 xs:mt-0" onSubmit={handleSubmit} ref={formRef}>
                <PasswordInput
                    name="password"
                    label={t("user.New password")}
                    isFormControl
                    autoFocus
                    isValidating={isValidating}
                    error={errors.password}
                />
                <PasswordInput
                    name="password-confirm"
                    label={t("user.Confirm new password")}
                    className="mt-3"
                    isFormControl
                    isValidating={isValidating}
                    error={errors["password-confirm"]}
                />
                <Flex items="center" gap="3" justify="end" mt="8">
                    <SubmitButton type="submit" isValidating={isValidating}>
                        {t("common.Next")}
                    </SubmitButton>
                </Flex>
            </Form.Root>
        );
    }
}

export default ResetPasswordForm;
