import { useTranslation } from "react-i18next";
import AutoComplete from "@/components/base/AutoComplete";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import Form from "@/components/base/Form";
import Input from "@/components/base/Input";
import SubmitButton from "@/components/base/SubmitButton";
import Switch from "@/components/base/Switch";
import Toast from "@/components/base/Toast";
import { useRef } from "react";
import useCreateUserInSettings from "@/controllers/api/settings/users/useCreateUserInSettings";
import { ROUTES } from "@/core/routing/constants";
import FormErrorMessage from "@/components/FormErrorMessage";
import useForm from "@/core/hooks/form/useForm";
import useSignUpExistsEmail from "@/controllers/api/auth/useSignUpExistsEmail";
import { User } from "@/core/models";
import PasswordInput from "@/components/PasswordInput";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import { ISharedSettingsModalProps } from "@/pages/SettingsPage/types";

function UserCreateFormDialog({ opened, setOpened }: ISharedSettingsModalProps): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { mutate } = useCreateUserInSettings();
    const { mutateAsync: checkExistsEmailMutateAsync } = useSignUpExistsEmail();
    const industryRef = useRef("");
    const industryInputRef = useRef<HTMLInputElement>(null);
    const purposeRef = useRef("");
    const purposeInputRef = useRef<HTMLInputElement>(null);
    const { errors, isValidating, handleSubmit, formRef, focusComponentRef } = useForm({
        errorLangPrefix: "settings.errors.user",
        schema: {
            email: {
                required: true,
                email: true,
                custom: {
                    errorKey: "exists",
                    validate: async (value) => {
                        if (!Utils.Type.isString(value) || !value.trim().length) {
                            return false;
                        }

                        const result = await checkExistsEmailMutateAsync({ email: value });

                        return !result.exists;
                    },
                },
            },
            firstname: {
                required: true,
            },
            lastname: {
                required: true,
            },
            password: {
                required: true,
            },
            "password-confirm": { required: true, sameWith: "password" },
            industry: {
                required: true,
            },
            purpose: {
                required: true,
            },
            affiliation: {},
            position: {},
            is_admin: {},
            should_activate: {},
        },
        inputRefs: {
            industry: industryInputRef,
            purpose: purposeInputRef,
        },
        mutate,
        mutateOnSuccess: () => {
            Toast.Add.success(t("successes.User created successfully."));
            setOpened(false);
        },
        mutateOnSettled: () => {
            if (!focusComponentRef.current) {
                return;
            }

            setTimeout(() => {
                if (Utils.Type.isElement(focusComponentRef.current)) {
                    focusComponentRef.current.focus();
                } else if (Utils.Type.isString(focusComponentRef.current)) {
                    formRef.current?.[focusComponentRef.current]?.focus();
                }
            }, 0);
        },
        useDefaultBadRequestHandler: true,
        apiErrorHandlers: {
            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
            },
        },
    });

    const setIndustry = (value: string) => {
        industryRef.current = value;
        industryInputRef.current!.value = value;
    };

    const setPurpose = (value: string) => {
        purposeRef.current = value;
        purposeInputRef.current!.value = value;
    };

    const changeOpenedState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        setOpened(opened);
    };

    return (
        <Dialog.Root open={opened} onOpenChange={changeOpenedState}>
            <Dialog.Content className="sm:max-w-md" aria-describedby="">
                <Form.Root noValidate onSubmit={handleSubmit} ref={formRef}>
                    <Dialog.Header>
                        <Dialog.Title>{t("settings.Create user")}</Dialog.Title>
                    </Dialog.Header>
                    <Flex direction="col" gap="4" w="full" mt="4">
                        <Box>
                            <Form.Field name="email">
                                <Floating.LabelInput
                                    label={t("user.Email")}
                                    isFormControl
                                    autoFocus
                                    autoComplete="section-new-user email"
                                    disabled={isValidating}
                                    required
                                />
                                {errors.email && <FormErrorMessage error={errors.email} />}
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="firstname">
                                <Floating.LabelInput
                                    label={t("user.First Name")}
                                    isFormControl
                                    autoComplete="firstname"
                                    disabled={isValidating}
                                    required
                                />
                                {errors.firstname && <FormErrorMessage error={errors.firstname} />}
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="lastname">
                                <Floating.LabelInput
                                    label={t("user.Last Name")}
                                    isFormControl
                                    autoComplete="lastname"
                                    disabled={isValidating}
                                    required
                                />
                                {errors.lastname && <FormErrorMessage error={errors.lastname} />}
                            </Form.Field>
                        </Box>
                        <Box>
                            <PasswordInput
                                name="password"
                                label={t("user.Password")}
                                autoComplete="new-password"
                                isFormControl
                                isValidating={isValidating}
                                error={errors.password}
                                required
                            />
                        </Box>
                        <Box>
                            <PasswordInput
                                name="password-confirm"
                                label={t("auth.Confirm password")}
                                autoComplete="new-password"
                                isFormControl
                                isValidating={isValidating}
                                error={errors["password-confirm"]}
                                required
                            />
                        </Box>
                        <Box>
                            <Form.Field name="industry">
                                <Input type="hidden" name="industry" ref={industryInputRef} />
                                <AutoComplete
                                    onValueChange={setIndustry}
                                    items={User.INDUSTRIES.map((industry) => ({ value: industry, label: t(`user.industries.${industry}`) }))}
                                    emptyMessage={industryRef.current ?? ""}
                                    disabled={isValidating}
                                    placeholder={t("user.What industry are you in?")}
                                    required
                                />
                                {errors.industry && <FormErrorMessage error={errors.industry} />}
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="purpose">
                                <Input type="hidden" name="purpose" ref={purposeInputRef} />
                                <AutoComplete
                                    onValueChange={setPurpose}
                                    items={User.PURPOSES.map((purpose) => ({ value: purpose, label: t(`user.purposes.${purpose}`) }))}
                                    emptyMessage={purposeRef.current ?? ""}
                                    disabled={isValidating}
                                    placeholder={t("user.What is your purpose for using {app}?")}
                                    required
                                />
                                {errors.purpose && <FormErrorMessage error={errors.purpose} />}
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="affiliation">
                                <Input
                                    className="w-full"
                                    placeholder={t("user.What organization are you affiliated with?")}
                                    autoComplete="affiliation"
                                    isFormControl
                                    disabled={isValidating}
                                />
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="position">
                                <Input
                                    className="w-full"
                                    placeholder={t("user.What is your position in your organization?")}
                                    autoComplete="position"
                                    isFormControl
                                    disabled={isValidating}
                                />
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="is_admin">
                                <Form.Control asChild>
                                    <Switch label={t("settings.Admin")} />
                                </Form.Control>
                            </Form.Field>
                        </Box>
                        <Box>
                            <Form.Field name="should_activate">
                                <Form.Control asChild>
                                    <Switch label={t("settings.Activation")} />
                                </Form.Control>
                            </Form.Field>
                        </Box>
                    </Flex>
                    <Dialog.Footer className="mt-6 flex-col gap-2 sm:justify-end sm:gap-0">
                        <Dialog.Close asChild>
                            <Button type="button" variant="secondary" disabled={isValidating}>
                                {t("common.Cancel")}
                            </Button>
                        </Dialog.Close>
                        <SubmitButton type="submit" isValidating={isValidating}>
                            {t("common.Create")}
                        </SubmitButton>
                    </Dialog.Footer>
                </Form.Root>
            </Dialog.Content>
        </Dialog.Root>
    );
}

export default UserCreateFormDialog;
