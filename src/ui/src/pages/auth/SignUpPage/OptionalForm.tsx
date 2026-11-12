import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import AvatarUploader from "@/components/AvatarUploader";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import Input from "@/components/base/Input";
import SubmitButton from "@/components/base/SubmitButton";
import useForm from "@/core/hooks/form/useForm";
import { ROUTES } from "@/core/routing/constants";
import { Utils } from "@langboard/core/utils";
import { ISignUpFormProps } from "@/pages/auth/SignUpPage/types";
import { setInitialErrorsWithFocusingElement } from "@/pages/auth/SignUpPage/utils";

function OptionalForm({ values, moveStep, initialErrorsRef }: ISignUpFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const dataTransferRef = useRef(new DataTransfer());
    const avatarUrlRef = useRef((values as unknown as Record<string, string>).avatarUrl ?? null);
    const { errors, setErrors, isValidating, handleSubmit, formRef } = useForm<Record<string, unknown>>({
        errorLangPrefix: "auth.errors",
        schema: {
            avatar: { mimeType: "image/*" },
            affiliation: {},
            position: {},
        },
        inputRefs: {
            avatar: dataTransferRef,
        },
        successCallback: (data) => {
            const newValues = {
                ...values,
                ...data,
                avatar: dataTransferRef.current.files[0],
                avatarUrl: avatarUrlRef.current,
            };

            moveStep(newValues as ISignUpFormProps["values"], ROUTES.SIGN_UP.OVERVIEW);
        },
    });

    useEffect(() => {
        setInitialErrorsWithFocusingElement(["avatar", "affiliation", "position"], initialErrorsRef, setErrors, formRef);
    }, []);

    if (values.avatar) {
        if (!dataTransferRef.current.items.length) {
            dataTransferRef.current.items.add(values.avatar);
        }
    }

    const skipStep = () => {
        const newValues = { ...values };
        moveStep(newValues, ROUTES.SIGN_UP.OVERVIEW);
    };

    return (
        <Form.Root className="mt-11 flex flex-col gap-4 xs:mt-0" onSubmit={handleSubmit} ref={formRef}>
            <AvatarUploader
                userInitials={Utils.String.getInitials(values.firstname, values.lastname)}
                initialAvatarUrl={(values as unknown as Record<string, string>).avatarUrl ?? undefined}
                dataTransferRef={dataTransferRef}
                avatarUrlRef={avatarUrlRef}
                errorMessage={errors.avatar}
                isValidating={isValidating}
                avatarSize="3xl"
            />
            <Form.Field name="affiliation">
                <Input
                    placeholder={t("user.What organization are you affiliated with?")}
                    autoFocus
                    autoComplete="affiliation"
                    isFormControl
                    defaultValue={values.affiliation ?? ""}
                    disabled={isValidating}
                />
            </Form.Field>
            <Form.Field name="position">
                <Input
                    placeholder={t("user.What is your position in your organization?")}
                    autoComplete="position"
                    isFormControl
                    defaultValue={values.position ?? ""}
                    disabled={isValidating}
                />
            </Form.Field>
            <Flex
                items="center"
                justify={{
                    initial: "between",
                    xs: "end",
                }}
                gap={{
                    initial: "6",
                    xs: "8",
                }}
                mt="8"
            >
                <Button type="button" variant="outline" onClick={() => moveStep(values, ROUTES.SIGN_UP.ADDITIONAL)} disabled={isValidating}>
                    {t("common.Back")}
                </Button>
                <Button type="button" variant="ghost" onClick={skipStep} disabled={isValidating}>
                    {t("common.Skip")}
                </Button>
                <SubmitButton type="submit" isValidating={isValidating}>
                    {t("common.Next")}
                </SubmitButton>
            </Flex>
        </Form.Root>
    );
}

export default OptionalForm;
