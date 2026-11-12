import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import FormErrorMessage from "@/components/FormErrorMessage";
import PasswordInput from "@/components/PasswordInput";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import Form from "@/components/base/Form";
import SubmitButton from "@/components/base/SubmitButton";
import useSignUpExistsEmail from "@/controllers/api/auth/useSignUpExistsEmail";
import useForm from "@/core/hooks/form/useForm";
import { ROUTES } from "@/core/routing/constants";
import { ISignUpFormProps } from "@/pages/auth/SignUpPage/types";
import { setInitialErrorsWithFocusingElement } from "@/pages/auth/SignUpPage/utils";
import { Utils } from "@langboard/core/utils";

function RequiredForm({ values, moveStep, initialErrorsRef }: ISignUpFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const { mutate: existsEmailMutate } = useSignUpExistsEmail();
    const { errors, setErrors, isValidating, handleSubmit, formRef, formDataRef, focusComponentRef } = useForm({
        errorLangPrefix: "auth.errors",
        schema: {
            email: { required: true, email: true },
            firstname: { required: true },
            lastname: { required: true },
            password: { required: true },
            "password-confirm": { required: true, sameWith: "password" },
        },
        mutate: existsEmailMutate,
        mutateOnSuccess: (data) => {
            if (data.exists) {
                setErrors({ email: "auth.errors.invalid.email-exists" });
                if (Utils.Type.isElement(focusComponentRef.current)) {
                    focusComponentRef.current.focus();
                } else if (Utils.Type.isString(focusComponentRef.current)) {
                    formRef.current?.[focusComponentRef.current]?.focus();
                }
                return;
            }

            moveStep(
                {
                    ...values,
                    ...formDataRef.current,
                },
                ROUTES.SIGN_UP.ADDITIONAL
            );
        },
        useDefaultBadRequestHandler: true,
    });

    useEffect(() => {
        setInitialErrorsWithFocusingElement(["email", "firstname", "lastname", "password"], initialErrorsRef, setErrors, formRef);
    }, []);

    return (
        <Form.Root className="mt-11 flex flex-col gap-4 xs:mt-0" onSubmit={handleSubmit} ref={formRef}>
            <Form.Field name="email">
                <Floating.LabelInput
                    label={t("user.Email")}
                    isFormControl
                    autoFocus
                    autoComplete="email"
                    required
                    disabled={isValidating}
                    defaultValue={values.email ?? ""}
                />
                {errors.email && <FormErrorMessage error={errors.email} icon="circle-alert" />}
            </Form.Field>
            <Form.Field name="firstname">
                <Floating.LabelInput
                    label={t("user.First Name")}
                    isFormControl
                    autoComplete="firstname"
                    required
                    disabled={isValidating}
                    defaultValue={values.firstname ?? ""}
                />
                {errors.firstname && <FormErrorMessage error={errors.firstname} icon="circle-alert" />}
            </Form.Field>
            <Form.Field name="lastname">
                <Floating.LabelInput
                    label={t("user.Last Name")}
                    isFormControl
                    autoComplete="lastname"
                    required
                    disabled={isValidating}
                    defaultValue={values.lastname ?? ""}
                />
                {errors.lastname && <FormErrorMessage error={errors.lastname} icon="circle-alert" />}
            </Form.Field>
            <PasswordInput
                name="password"
                label={t("user.Password")}
                isFormControl
                required
                isValidating={isValidating}
                defaultValue={values.password}
                error={errors.password}
            />
            <PasswordInput
                name="password-confirm"
                label={t("auth.Confirm password")}
                isFormControl
                required
                isValidating={isValidating}
                defaultValue={values.password}
                error={errors["password-confirm"]}
            />
            <Flex items="center" justify="end" gap="3" mt="8">
                <SubmitButton type="submit" isValidating={isValidating}>
                    {t("common.Next")}
                </SubmitButton>
            </Flex>
        </Form.Root>
    );
}

export default RequiredForm;
