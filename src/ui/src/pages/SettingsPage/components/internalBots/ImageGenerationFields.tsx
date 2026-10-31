import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { OPENAI_COMPATIBLE_PROVIDERS } from "@langboard/core/ai";
import Button from "@/components/base/Button";
import Floating from "@/components/base/Floating";
import Select from "@/components/base/Select";
import Switch from "@/components/base/Switch";
import PasswordInput from "@/components/PasswordInput";
import ProviderIcon from "@/components/bots/BotValueInput/ProviderIcon";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";

export const IMAGE_PROVIDERS = ["OpenAI", "OpenAI Compatible", "LiteLLM"] as const;
export interface ImageGenerationConfig {
    agent_llm: (typeof IMAGE_PROVIDERS)[number];
    base_url: string;
    model_name: string;
    enabled: boolean;
    size: string;
    quality?: string;
    response_format?: "b64_json" | "url";
}
export const defaultImageGenerationConfig = (): ImageGenerationConfig => ({
    agent_llm: "OpenAI",
    base_url: OPENAI_COMPATIBLE_PROVIDERS.OpenAI,
    model_name: "",
    enabled: false,
    size: "1024x1024",
});

interface Props {
    config: ImageGenerationConfig;
    onChange: (config: ImageGenerationConfig) => void;
    apiKey: string;
    onApiKeyChange: (value: string) => void;
    keyConfigured?: boolean;
    profileUID?: string;
    disabled?: boolean;
    saved?: boolean;
    profileRevision?: string;
}

export default function ImageGenerationFields({
    config,
    onChange,
    apiKey,
    onApiKeyChange,
    keyConfigured,
    profileUID,
    disabled,
    saved,
    profileRevision,
}: Props) {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser);
    const identity = JSON.stringify([actor?.uid, profileUID, config.agent_llm, config.base_url, apiKey, disabled, saved, profileRevision]);
    const currentIdentity = useRef(identity);
    currentIdentity.current = identity;
    const [models, setModels] = useState<string[]>([]);
    const [loadingModels, setLoadingModels] = useState(false);
    const [catalogError, setCatalogError] = useState(false);
    const request = useRef<AbortController | null>(null);
    useEffect(() => {
        request.current?.abort();
        setLoadingModels(false);
        setModels([]);
        setCatalogError(false);
        return () => request.current?.abort();
    }, [actor, config.agent_llm, config.base_url, apiKey, profileUID, saved, disabled, profileRevision]);
    const update = <K extends keyof ImageGenerationConfig>(key: K, value: ImageGenerationConfig[K]) => onChange({ ...config, [key]: value });
    const fetchModels = async () => {
        if (disabled || !actor || !config.base_url.trim() || (!!profileUID && !saved)) return;
        const startedAs = identity;
        request.current?.abort();
        const controller = new AbortController();
        request.current = controller;
        setLoadingModels(true);
        setCatalogError(false);
        try {
            const form = new FormData();
            form.append("base_url", config.base_url);
            form.append("api_key", apiKey);
            const response = profileUID
                ? await api.get(`/settings/image-generation/${encodeURIComponent(profileUID)}/models`, {
                      signal: controller.signal,
                      env: { secretInput: true, interceptToast: true } as never,
                  })
                : await api.post("/settings/model-providers/models", form, {
                      signal: controller.signal,
                      env: { secretInput: true, interceptToast: true } as never,
                  });
            if (!controller.signal.aborted && currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor)
                setModels(Array.isArray(response.data.models) ? response.data.models : []);
        } catch {
            if (!controller.signal.aborted && currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor)
                setCatalogError(true);
        } finally {
            if (!controller.signal.aborted && currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor)
                setLoadingModels(false);
        }
    };
    return (
        <div className="space-y-4">
            <Floating.LabelSelect
                label={t("bot.agent.Select a provider")}
                value={config.agent_llm}
                disabled={disabled}
                onValueChange={(provider) => {
                    const next = provider as ImageGenerationConfig["agent_llm"];
                    onApiKeyChange("");
                    onChange({ ...config, agent_llm: next, base_url: OPENAI_COMPATIBLE_PROVIDERS[next], model_name: "" });
                }}
                options={IMAGE_PROVIDERS.map((provider) => (
                    <Select.Item key={provider} value={provider}>
                        <span className="flex items-center gap-2">
                            <ProviderIcon provider={provider} />
                            {provider}
                        </span>
                    </Select.Item>
                ))}
            />
            <Floating.LabelInput
                label={t("internalBot.image.base_url")}
                aria-label={t("internalBot.image.base_url")}
                type="url"
                value={config.base_url}
                disabled={disabled}
                required
                onChange={(event) => {
                    onApiKeyChange("");
                    update("base_url", event.currentTarget.value);
                }}
            />
            <PasswordInput
                label={t("settings.Internal bot API key")}
                value={apiKey}
                disabled={disabled}
                maxLength={4096}
                onChange={(event) => onApiKeyChange(event.currentTarget.value)}
            />
            <p className="text-xs text-muted-foreground">{t(keyConfigured ? "internalBot.image.key_saved" : "internalBot.image.key_private")}</p>
            <Floating.LabelInput
                label={t("bot.agent.Model")}
                aria-label={t("bot.agent.Model")}
                value={config.model_name}
                disabled={disabled}
                required
                maxLength={256}
                onChange={(event) => update("model_name", event.currentTarget.value)}
            />
            <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled || !actor || loadingModels || !config.base_url || (!!profileUID && !saved)}
                onClick={() => void fetchModels()}
            >
                {t("internalBot.image.fetch_models")}
            </Button>
            {!!profileUID && !saved && <p className="text-xs text-muted-foreground">{t("internalBot.image.save_before_test")}</p>}
            {catalogError && (
                <p role="alert" className="text-sm text-destructive">
                    {t("internalBot.image.catalog_failed")}
                </p>
            )}
            {!!models.length && (
                <Floating.LabelSelect
                    label={t("internalBot.image.available_models")}
                    value={models.includes(config.model_name) ? config.model_name : ""}
                    disabled={disabled}
                    onValueChange={(value) => update("model_name", value)}
                    options={models.map((model) => (
                        <Select.Item key={model} value={model}>
                            {model}
                        </Select.Item>
                    ))}
                />
            )}
            <div className="grid gap-4 sm:grid-cols-2">
                <Floating.LabelInput
                    label={t("internalBot.image.size")}
                    value={config.size}
                    readOnly
                    disabled={disabled}
                    maxLength={32}
                    onChange={(event) => update("size", event.currentTarget.value)}
                />
                <Floating.LabelSelect
                    label={t("internalBot.image.quality")}
                    value={config.quality ?? "default"}
                    disabled={disabled}
                    onValueChange={(value) => update("quality", value === "default" ? undefined : value)}
                    options={["default", "auto", "low", "medium", "high", "standard", "hd"].map((value) => (
                        <Select.Item key={value} value={value}>
                            {value === "default" ? t("internalBot.image.provider_default") : t(`settings.imageGenerationOptions.quality.${value}`)}
                        </Select.Item>
                    ))}
                />
            </div>
            <Floating.LabelSelect
                label={t("internalBot.image.response_format")}
                value={config.response_format ?? "default"}
                disabled={disabled}
                onValueChange={(value) => update("response_format", value === "default" ? undefined : (value as "url" | "b64_json"))}
                options={["default", "b64_json", "url"].map((value) => (
                    <Select.Item key={value} value={value}>
                        {value === "default"
                            ? t("internalBot.image.provider_default")
                            : value === "url"
                              ? t("settings.URL")
                              : t("settings.imageGenerationOptions.base64Json")}
                    </Select.Item>
                ))}
            />
            <p className="text-xs text-muted-foreground">{t("internalBot.image.supported_options")}</p>
            <label className="flex items-center justify-between gap-3 text-sm">
                <span>{t("internalBot.image.enabled")}</span>
                <Switch checked={config.enabled} disabled={disabled} onCheckedChange={(value) => update("enabled", value)} />
            </label>
        </div>
    );
}
