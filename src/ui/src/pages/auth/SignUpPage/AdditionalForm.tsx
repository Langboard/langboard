import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import FormErrorMessage from "@/components/FormErrorMessage";
import AutoComplete from "@/components/base/AutoComplete";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import Input from "@/components/base/Input";
import SubmitButton from "@/components/base/SubmitButton";
import { ISignUpForm } from "@/controllers/api/auth/useSignUp";
import useForm from "@/core/hooks/form/useForm";
import { User } from "@/core/models";
import { ROUTES } from "@/core/routing/constants";
import { ISignUpFormProps } from "@/pages/auth/SignUpPage/types";
import { setInitialErrorsWithFocusingElement } from "@/pages/auth/SignUpPage/utils";

function AdditionalForm({ values, moveStep, initialErrorsRef }: ISignUpFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const industryRef = useRef<string>(values.industry ?? "");
    const industryInputRef = useRef<HTMLInputElement>(null);
    const purposeRef = useRef<string>(values.purpose ?? "");
    const purposeInputRef = useRef<HTMLInputElement>(null);
    const { errors, setErrors, isValidating, handleSubmit, formRef } = useForm<Pick<ISignUpForm, "industry" | "purpose">>({
        errorLangPrefix: "auth.errors",
        schema: {
            industry: { required: true },
            purpose: { required: true },
        },
        successCallback: (data) => {
            moveStep(
                {
                    ...values,
                    ...data,
                },
                ROUTES.SIGN_UP.OPTIONAL
            );
        },
    });

    useEffect(() => {
        setInitialErrorsWithFocusingElement(["industry", "purpose"], initialErrorsRef, setErrors, formRef);
    }, []);

    const setIndustry = (value: string) => {
        industryRef.current = value;
        industryInputRef.current!.value = value;
    };

    const setPurpose = (value: string) => {
        purposeRef.current = value;
        purposeInputRef.current!.value = value;
    };

    return (
        <Form.Root className="mt-11 flex flex-col gap-4 xs:mt-0" onSubmit={handleSubmit} ref={formRef}>
            <Form.Field name="industry">
                <Input type="hidden" name="industry" value={industryRef.current} ref={industryInputRef} />
                <AutoComplete
                    selectedValue={values.industry}
                    onValueChange={setIndustry}
                    items={User.INDUSTRIES.map((industry) => ({ value: industry, label: t(`user.industries.${industry}`) }))}
                    emptyMessage={industryRef.current ?? ""}
                    required
                    disabled={isValidating}
                    placeholder={t("user.What industry are you in?")}
                />
                {errors.industry && <FormErrorMessage error={errors.industry} icon="circle-alert" />}
            </Form.Field>
            <Form.Field name="purpose">
                <Input type="hidden" name="purpose" value={purposeRef.current} ref={purposeInputRef} />
                <AutoComplete
                    selectedValue={values.purpose}
                    onValueChange={setPurpose}
                    items={User.PURPOSES.map((purpose) => ({ value: purpose, label: t(`user.purposes.${purpose}`) }))}
                    emptyMessage={purposeRef.current ?? ""}
                    required
                    disabled={isValidating}
                    placeholder={t("user.What is your purpose for using {app}?")}
                />
                {errors.purpose && <FormErrorMessage error={errors.purpose} icon="circle-alert" />}
            </Form.Field>
            <Flex items="center" gap="3" justify={{ initial: "between", xs: "end" }} mt="8">
                <Button type="button" variant="outline" onClick={() => moveStep(values, ROUTES.SIGN_UP.REQUIRED)} disabled={isValidating}>
                    {t("common.Back")}
                </Button>
                <SubmitButton type="submit" isValidating={isValidating}>
                    {t("common.Next")}
                </SubmitButton>
            </Flex>
        </Form.Root>
    );
}

export default AdditionalForm;
