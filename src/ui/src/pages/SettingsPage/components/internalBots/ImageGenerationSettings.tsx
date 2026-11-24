import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isAxiosError } from "axios";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import useUpdateInternalBot from "@/controllers/api/settings/internalBots/useUpdateInternalBot";
import type { InternalBotModel } from "@/core/models";
import ImageGenerationFields, { defaultImageGenerationConfig, type ImageGenerationConfig } from "./ImageGenerationFields";

function readConfig(value: string): ImageGenerationConfig {
    try {
        return { ...defaultImageGenerationConfig(), ...JSON.parse(value) };
    } catch {
        return defaultImageGenerationConfig();
    }
}

export default function ImageGenerationSettings({ bot, canUpdate }: { bot: InternalBotModel.TModel; canUpdate: boolean }) {
    const [t] = useTranslation();
    const value = bot.useField("value");
    const keyConfigured = bot.useField("api_key_configured");
    const actor = useAuthStore((state) => state.currentUser);
    const actorUID = actor?.uid;
    const updatedAt = bot.useField("updated_at");
    const [config, setConfig] = useState(() => readConfig(value));
    const [apiKey, setApiKey] = useState("");
    const [prompt, setPrompt] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [preview, setPreview] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const request = useRef<AbortController | null>(null);
    const identity = `${actorUID}:${bot.uid}:${value}:${updatedAt?.getTime()}:${canUpdate}`;
    const currentIdentity = useRef(identity);
    currentIdentity.current = identity;
    const { mutateAsync, isPending, reset } = useUpdateInternalBot(bot, { interceptToast: true, gcTime: 0 });
    const dirty = JSON.stringify(config) !== JSON.stringify(readConfig(value)) || !!apiKey;
    useEffect(() => {
        request.current?.abort();
        setConfig(readConfig(value));
        setApiKey("");
        setPreview("");
        setError("");
        setConfirmed(false);
        setPrompt("");
        setBusy(false);
        return () => request.current?.abort();
    }, [actor, identity, value]);
    const changeConfig = (next: ImageGenerationConfig) => {
        setConfig(next);
        setPreview("");
        setConfirmed(false);
    };
    const save = async () => {
        if (!canUpdate || !actorUID || busy || isPending) return;
        setError("");
        const startedAs = identity;
        try {
            await mutateAsync({ value: JSON.stringify(config), api_key: apiKey });
            if (currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor) setApiKey("");
        } catch {
            if (currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor) setError("save_failed");
        } finally {
            reset();
        }
    };
    const generate = async () => {
        if (!canUpdate || !actorUID || !confirmed || !prompt.trim() || !config.enabled || dirty || busy || isPending) return;
        const controller = new AbortController();
        request.current = controller;
        const startedAs = identity;
        setBusy(true);
        setConfirmed(false);
        setError("");
        setPreview("");
        try {
            const form = new FormData();
            form.append("prompt", prompt.trim());
            form.append("confirmed", "true");
            const response = await api.post(`/settings/image-generation/${encodeURIComponent(bot.uid)}/preview`, form, {
                signal: controller.signal,
                timeout: 180_000,
                env: { secretInput: true, interceptToast: true } as never,
                validateStatus: () => true,
            });
            if (controller.signal.aborted || currentIdentity.current !== startedAs || useAuthStore.getState().currentUser !== actor) return;
            if (response.status !== 200) {
                setError(response.status === 429 ? "quota" : response.status === 504 ? "timeout" : "generation_failed");
                return;
            }
            const source = response.data.preview_data_url;
            if (typeof source !== "string" || source.length > 3_000_000 || !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(source))
                throw new Error("Invalid image preview");
            setPreview(source);
        } catch (cause) {
            if (controller.signal.aborted || currentIdentity.current !== startedAs || useAuthStore.getState().currentUser !== actor) return;
            const status = isAxiosError(cause) ? cause.response?.status : undefined;
            setError(status === 429 ? "quota" : status === 504 ? "timeout" : "generation_failed");
        } finally {
            if (!controller.signal.aborted && currentIdentity.current === startedAs && useAuthStore.getState().currentUser === actor) setBusy(false);
        }
    };
    return (
        <section className="w-full max-w-xl space-y-5 rounded-lg border p-4" aria-label={t("internalBot.image.title")}>
            <h3 className="font-semibold">{t("internalBot.image.title")}</h3>
            <ImageGenerationFields
                config={config}
                onChange={changeConfig}
                apiKey={apiKey}
                onApiKeyChange={setApiKey}
                keyConfigured={keyConfigured}
                profileUID={bot.uid}
                profileRevision={value}
                saved={!dirty}
                disabled={!canUpdate || !actorUID || busy || isPending}
            />
            <div className="flex flex-wrap gap-2">
                <Button
                    type="button"
                    onClick={() => void save()}
                    disabled={!canUpdate || !actorUID || busy || isPending || !dirty || !config.base_url.trim() || !config.model_name.trim()}
                >
                    {t("common.Save")}
                </Button>
                <Button
                    type="button"
                    variant="ghost"
                    disabled={!dirty || busy || isPending}
                    onClick={() => {
                        setPreview("");
                        setConfirmed(false);
                        setConfig(readConfig(value));
                        setApiKey("");
                        setError("");
                    }}
                >
                    {t("common.Cancel")}
                </Button>
            </div>
            <div className="space-y-3 border-t pt-4">
                <h4 className="text-sm font-medium">{t("internalBot.image.test_title")}</h4>
                <Input
                    value={prompt}
                    maxLength={2000}
                    aria-label={t("internalBot.image.prompt")}
                    placeholder={t("internalBot.image.prompt")}
                    disabled={!canUpdate || !actorUID || busy || isPending}
                    onChange={(event) => {
                        setPrompt(event.currentTarget.value);
                        setConfirmed(false);
                        setPreview("");
                    }}
                />
                <label className="flex items-start gap-2 text-sm">
                    <Checkbox
                        checked={confirmed}
                        disabled={!canUpdate || !actorUID || busy || isPending}
                        onCheckedChange={(checked) => setConfirmed(checked === true)}
                    />
                    <span>{t("internalBot.image.consent")}</span>
                </label>
                <p className="text-xs text-muted-foreground">{t("internalBot.image.preview_only")}</p>
                <div className="flex flex-wrap gap-2">
                    <Button
                        type="button"
                        variant="outline"
                        disabled={!canUpdate || !actorUID || !confirmed || !prompt.trim() || !config.enabled || dirty || busy || isPending}
                        onClick={() => void generate()}
                    >
                        {t(busy ? "internalBot.image.generating" : "internalBot.image.generate_test")}
                    </Button>
                    {busy && (
                        <Button
                            type="button"
                            variant="ghost"
                            onClick={() => {
                                request.current?.abort();
                                setBusy(false);
                                setError("cancelled");
                            }}
                        >
                            {t("common.Cancel")}
                        </Button>
                    )}
                </div>
                {preview && (
                    <div className="flex items-end gap-5" aria-label={t("internalBot.image.preview")}>
                        {[32, 48, 96].map((size) => (
                            <figure key={size} className="space-y-1 text-center text-xs text-muted-foreground">
                                <img
                                    src={preview}
                                    width={size}
                                    height={size}
                                    alt={t("internalBot.image.preview")}
                                    className="rounded-full border object-cover"
                                />
                                <figcaption>{size}px</figcaption>
                            </figure>
                        ))}
                    </div>
                )}
            </div>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {t(`internalBot.image.${error}`)}
                </p>
            )}
        </section>
    );
}
