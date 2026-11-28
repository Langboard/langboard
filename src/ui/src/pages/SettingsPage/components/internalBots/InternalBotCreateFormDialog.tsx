/* eslint-disable @typescript-eslint/no-explicit-any */
import { useTranslation } from "react-i18next";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Floating from "@/components/base/Floating";
import Form from "@/components/base/Form";
import Select from "@/components/base/Select";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import { useEffect, useMemo, useRef, useState } from "react";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ROUTES } from "@/core/routing/constants";
import FormErrorMessage from "@/components/FormErrorMessage";
import AvatarUploader from "@/components/AvatarUploader";
import { InternalBotModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import useCreateInternalBot from "@/controllers/api/settings/internalBots/useCreateInternalBot";
import PasswordInput from "@/components/PasswordInput";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { getValueType, requirements, syncPendingBotValueInputChange } from "@/components/bots/BotValueInput/utils";
import { AVAILABLE_RUNNING_TYPES_BY_PLATFORM, EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";
import BotValueInput from "@/components/bots/BotValueInput";
import { TBotValueDefaultInputRefLike } from "@/components/bots/BotValueInput/types";
import BotPlatformSelect from "@/components/bots/BotPlatformSelect";
import BotPlatformRunningTypeSelect from "@/components/bots/BotPlatformRunningTypeSelect";
import { ISharedSettingsModalProps } from "@/pages/SettingsPage/types";
import useAuthStore from "@/core/stores/AuthStore";
import ImageGenerationFields, { defaultImageGenerationConfig } from "./ImageGenerationFields";

const compactorProviderPreset = JSON.stringify({
    agent_llm: "Z.ai Coding Plan",
    base_url: "https://api.z.ai/api/coding/paas/v4",
    model_name: "glm-5.3",
});

function InternalBotCreateFormDialog({ opened, setOpened, currentUser }: ISharedSettingsModalProps): React.JSX.Element {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser);
    const navigate = usePageNavigateRef();
    const [isValidating, setIsValidating] = useState(false);
    const dialogContentRef = useRef<HTMLDivElement | null>(null);
    const dataTransferRef = useRef(new DataTransfer());
    const inputsRef = useRef({
        displayName: null as HTMLInputElement | null,
        url: null as HTMLInputElement | null,
        apiKey: null as HTMLInputElement | null,
        value: null as HTMLInputElement | HTMLTextAreaElement | TBotValueDefaultInputRefLike | null,
    });
    const setInputRef = (key: keyof typeof inputsRef.current) => (el: HTMLElement | TBotValueDefaultInputRefLike | null) => {
        inputsRef.current[key] = el as any;
    };
    const [selectedType, setSelectedType] = useState<InternalBotModel.EInternalBotType>(InternalBotModel.EInternalBotType.ProjectChat);
    const isImageGeneration = selectedType === InternalBotModel.EInternalBotType.ImageGeneration;
    const isNativeOnly =
        isImageGeneration ||
        selectedType === InternalBotModel.EInternalBotType.CommentCompaction ||
        selectedType === InternalBotModel.EInternalBotType.AvatarSummary;
    const [imageConfig, setImageConfig] = useState(defaultImageGenerationConfig);
    const [imageApiKey, setImageApiKey] = useState("");
    const [selectedPlatform, setSelectedPlatform] = useState<EBotPlatform>(EBotPlatform.Default);
    const [selectedPlatformRunningType, setSelectedPlatformRunningType] = useState<EBotPlatformRunningType>(EBotPlatformRunningType.Default);
    const valueType = useMemo(() => getValueType(selectedPlatform, selectedPlatformRunningType), [selectedPlatform, selectedPlatformRunningType]);
    const formRequirements = useMemo(
        () => (isImageGeneration ? [] : (requirements[selectedPlatform]?.[selectedPlatformRunningType] ?? [])),
        [selectedPlatform, selectedPlatformRunningType, isImageGeneration]
    );
    const valueRef = useRef("");
    const { mutate, reset } = useCreateInternalBot({ interceptToast: isImageGeneration, gcTime: isImageGeneration ? 0 : undefined });
    const [errors, setErrors] = useState<Record<string, string>>({});
    const save = async () => {
        if (isValidating || !inputsRef.current.displayName) {
            return;
        }

        if (!isImageGeneration) await syncPendingBotValueInputChange(inputsRef.current.value);
        setIsValidating(true);

        const values = {} as Record<keyof typeof inputsRef.current, string>;
        const newErrors = {} as Record<keyof typeof inputsRef.current, string>;
        let validImageEndpoint = false;
        try {
            validImageEndpoint = ["https:", "http:"].includes(new URL(imageConfig.base_url).protocol);
        } catch {
            /* Server validates endpoint policy. */
        }
        if (isImageGeneration && (!validImageEndpoint || !imageConfig.model_name.trim())) {
            newErrors.value = t("internalBot.image.required");
        }
        let focusableInput = null as HTMLInputElement | HTMLTextAreaElement | null;

        const shouldValidateInputs: (keyof typeof inputsRef.current)[] = ["displayName", ...formRequirements];
        shouldValidateInputs.forEach((key) => {
            const input = inputsRef.current[key] as HTMLInputElement | HTMLTextAreaElement;
            if (!input) {
                return;
            }

            if (input?.type === "default-bot-json") {
                return;
            }

            const value = input!.value.trim();
            if (!value) {
                newErrors[key as keyof typeof inputsRef.current] = t(`settings.errors.missing.internal_bot_${key}`);
                if (!focusableInput) {
                    focusableInput = input;
                }
            } else if (key === "url" && !Utils.String.isValidURL(value)) {
                newErrors[key as keyof typeof inputsRef.current] = t(`settings.errors.invalid.internal_bot_${key}`);
                if (!focusableInput) {
                    focusableInput = input;
                }
            } else {
                values[key] = value;
            }
        });

        let shouldStop = false;
        const valueInput = inputsRef.current.value;
        if (shouldValidateInputs.includes("value") && valueInput?.type === "default-bot-json") {
            const validated = (valueInput as TBotValueDefaultInputRefLike).validate(!focusableInput);
            if (!validated) {
                shouldStop = true;
            }
        }

        if (Object.keys(newErrors).length || shouldStop) {
            setErrors(newErrors);
            setIsValidating(false);
            focusableInput?.focus();
            return;
        }

        mutate(
            {
                bot_type: selectedType,
                display_name: values.displayName,
                platform: isNativeOnly ? EBotPlatform.Default : selectedPlatform,
                platform_running_type: isNativeOnly ? EBotPlatformRunningType.Default : selectedPlatformRunningType,
                api_url: values.url,
                api_key: isImageGeneration ? imageApiKey : values.apiKey,
                value: isImageGeneration ? JSON.stringify(imageConfig) : valueRef.current,
                avatar: dataTransferRef.current.files[0],
            },
            {
                onSuccess: () => {
                    if (isImageGeneration) setOpened(false);
                    setImageApiKey("");
                    setImageConfig(defaultImageGenerationConfig());
                    Toast.Add.success(t("successes.Internal bot created successfully."));
                    if (dataTransferRef.current.items.length) {
                        dataTransferRef.current.items.clear();
                    }
                    Object.values(inputsRef.current).forEach((input) => {
                        if (input) {
                            input.value = "";
                        }
                    });
                },
                onError: (error) => {
                    if (isImageGeneration) {
                        setErrors({ value: t("internalBot.image.save_failed") });
                        return;
                    }
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                    if (!isImageGeneration) setOpened(false);
                    if (isImageGeneration) reset();
                },
            }
        );
    };

    const changeOpenedState = (opened: bool) => {
        if (isValidating) {
            return;
        }

        setOpened(opened);
    };

    useEffect(() => {
        setImageApiKey("");
        if (!opened) {
            setImageConfig(defaultImageGenerationConfig());
            setErrors({});
        }
    }, [opened, selectedType, actor]);

    useEffect(() => {
        if ([InternalBotModel.EInternalBotType.CommentCompaction, InternalBotModel.EInternalBotType.AvatarSummary].includes(selectedType)) {
            setSelectedPlatform(EBotPlatform.Default);
            setSelectedPlatformRunningType(EBotPlatformRunningType.Default);
            if (
                selectedType === InternalBotModel.EInternalBotType.CommentCompaction &&
                inputsRef.current.displayName &&
                !inputsRef.current.displayName.value.trim()
            ) {
                inputsRef.current.displayName.value = "컴팩터";
            }
        }
    }, [selectedType]);

    useEffect(() => {
        if (AVAILABLE_RUNNING_TYPES_BY_PLATFORM[selectedPlatform].includes(selectedPlatformRunningType)) {
            return;
        }

        setSelectedPlatformRunningType(AVAILABLE_RUNNING_TYPES_BY_PLATFORM[selectedPlatform][0]);
    }, [selectedPlatform]);

    useEffect(() => {
        if (!opened) {
            return;
        }

        const resetScroll = () => dialogContentRef.current?.scrollTo({ top: 0, left: 0 });
        resetScroll();
        const animationFrame = requestAnimationFrame(() => {
            resetScroll();
            requestAnimationFrame(resetScroll);
        });

        return () => cancelAnimationFrame(animationFrame);
    }, [opened]);

    return (
        <Dialog.Root open={opened} onOpenChange={changeOpenedState}>
            <Dialog.Content
                ref={dialogContentRef}
                className="max-h-[calc(100vh-2rem)] w-[calc(100vw-2rem)] max-w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-md"
                aria-describedby=""
            >
                <Dialog.Header>
                    <Dialog.Title>{t("settings.Create internal bot")}</Dialog.Title>
                </Dialog.Header>
                <Form.Root className="mt-4">
                    <AvatarUploader
                        isBot
                        dataTransferRef={dataTransferRef}
                        isValidating={isValidating}
                        errorMessage={errors.avatar}
                        avatarSize="3xl"
                    />
                    <Box mt="10">
                        <Floating.LabelInput
                            label={t("settings.Internal bot display name")}
                            autoFocus
                            autoComplete="off"
                            disabled={isValidating}
                            required
                            ref={setInputRef("displayName")}
                        />
                        {errors.displayName && <FormErrorMessage error={errors.displayName} notInForm />}
                    </Box>
                    <Box mt="4">
                        <Floating.LabelSelect
                            label={t("settings.Select a type")}
                            value={selectedType}
                            onValueChange={setSelectedType as (value: string) => void}
                            disabled={isValidating}
                            required
                            options={Object.keys(InternalBotModel.EInternalBotType).map((typeKey) => {
                                const botType = InternalBotModel.EInternalBotType[typeKey];
                                return (
                                    <Select.Item value={botType} key={`internal-bot-type-select-${botType}`}>
                                        {t(`internalBot.botTypes.${botType}`)}
                                    </Select.Item>
                                );
                            })}
                        />
                    </Box>
                    {!isNativeOnly && (
                        <Box mt="4">
                            <BotPlatformSelect state={[selectedPlatform, setSelectedPlatform]} isValidating={isValidating} />
                        </Box>
                    )}
                    {!isNativeOnly && (
                        <Box mt="4">
                            <BotPlatformRunningTypeSelect
                                state={[selectedPlatformRunningType, setSelectedPlatformRunningType]}
                                platform={selectedPlatform}
                                isValidating={isValidating}
                            />
                        </Box>
                    )}
                    {selectedType === InternalBotModel.EInternalBotType.AvatarSummary && (
                        <p className="mt-4 text-sm">{t("internalBot.avatar.summary_configuration")}</p>
                    )}
                    {isImageGeneration && (
                        <Box mt="4">
                            <ImageGenerationFields
                                config={imageConfig}
                                onChange={setImageConfig}
                                apiKey={imageApiKey}
                                onApiKeyChange={setImageApiKey}
                                disabled={isValidating}
                            />
                            {errors.value && <FormErrorMessage error={errors.value} notInForm />}
                        </Box>
                    )}
                    {formRequirements.includes("url") && (
                        <Box mt="4">
                            <Floating.LabelInput
                                label={t("settings.Internal bot API URL")}
                                autoComplete="off"
                                disabled={isValidating}
                                required
                                ref={setInputRef("url")}
                            />
                            {errors.url && <FormErrorMessage error={errors.url} notInForm />}
                        </Box>
                    )}
                    {formRequirements.includes("apiKey") && (
                        <Box mt="4">
                            <PasswordInput
                                label={t("settings.Internal bot API key")}
                                isValidating={isValidating}
                                autoComplete="off"
                                required
                                ref={setInputRef("apiKey")}
                            />
                            {errors.apiKey && <FormErrorMessage error={errors.apiKey} notInForm />}
                        </Box>
                    )}
                    {formRequirements.includes("value") && (
                        <Box mt="4">
                            <BotValueInput
                                key={selectedType}
                                purpose={
                                    selectedType === InternalBotModel.EInternalBotType.DocumentEmbedding
                                        ? "embedding"
                                        : [
                                                InternalBotModel.EInternalBotType.CommentCompaction,
                                                InternalBotModel.EInternalBotType.AvatarSummary,
                                            ].includes(selectedType)
                                          ? "compaction"
                                          : "chat"
                                }
                                currentUser={currentUser}
                                platform={selectedPlatform}
                                platformRunningType={selectedPlatformRunningType}
                                value={selectedType === InternalBotModel.EInternalBotType.CommentCompaction ? compactorProviderPreset : ""}
                                label={t(`bot.platformRunningTypes.${selectedPlatformRunningType}`)}
                                valueType={valueType}
                                newValueRef={valueRef}
                                isValidating={isValidating}
                                previewByDialog
                                required
                                ref={setInputRef("value")}
                            />

                            {errors.value && <FormErrorMessage error={errors.value} notInForm />}
                        </Box>
                    )}
                </Form.Root>
                <Dialog.Footer className="mt-6 flex-col gap-2 sm:justify-end sm:gap-0">
                    <Dialog.Close asChild>
                        <Button type="button" variant="secondary" disabled={isValidating}>
                            {t("common.Cancel")}
                        </Button>
                    </Dialog.Close>
                    <SubmitButton type="button" isValidating={isValidating} onClick={save}>
                        {t("common.Create")}
                    </SubmitButton>
                </Dialog.Footer>
            </Dialog.Content>
        </Dialog.Root>
    );
}

export default InternalBotCreateFormDialog;
