import { useTranslation } from "react-i18next";
import PasswordInput from "@/components/PasswordInput";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useChangePassword from "@/controllers/api/account/useChangePassword";
import useForm from "@/core/hooks/form/useForm";
import { useAccountSetting } from "@/core/providers/AccountSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { useEffect } from "react";

function PasswordPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const { updatedUser } = useAccountSetting();
    const [t] = useTranslation();
    const { mutate } = useChangePassword();
    const { errors, isValidating, handleSubmit, formRef } = useForm({
        errorLangPrefix: "myAccount.errors",
        schema: {
            current_password: { required: true },
            new_password: { required: true },
            "password-confirm": { required: true, sameWith: "new_password" },
        },
        mutate,
        mutateOnSuccess: () => {
            Toast.Add.success(t("successes.Password updated successfully."));
            formRef.current?.reset();
            updatedUser();
        },
        useDefaultBadRequestHandler: true,
    });

    useEffect(() => {
        setPageAliasRef.current("Change Password");
    }, []);

    return (
        <>
            <h2 className="scroll-m-20 border-b pb-3 text-2xl font-semibold">{t("user.Password")}</h2>
            <Form.Root className="mt-11" onSubmit={handleSubmit} ref={formRef}>
                <Flex justify="center">
                    <Box w="full" className="max-w-sm">
                        <PasswordInput
                            name="current_password"
                            label={t("user.Current password")}
                            isFormControl
                            autoFocus
                            required
                            isValidating={isValidating}
                            error={errors.current_password}
                        />
                        <PasswordInput
                            name="new_password"
                            label={t("user.New password")}
                            className="mt-4"
                            isFormControl
                            required
                            isValidating={isValidating}
                            error={errors.new_password}
                        />
                        <PasswordInput
                            name="password-confirm"
                            label={t("user.Confirm new password")}
                            className="mt-4"
                            isFormControl
                            required
                            isValidating={isValidating}
                            error={errors["password-confirm"]}
                        />
                    </Box>
                </Flex>
                <Flex items="center" justify="center" gap="3" mt="8">
                    <SubmitButton type="submit" isValidating={isValidating}>
                        {t("common.Save")}
                    </SubmitButton>
                </Flex>
            </Form.Root>
        </>
    );
}

export default PasswordPage;
