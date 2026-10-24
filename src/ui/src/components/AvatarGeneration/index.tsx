import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Checkbox from "@/components/base/Checkbox";
import Textarea from "@/components/base/Textarea";
import useAuthStore from "@/core/stores/AuthStore";
import { SettingRole } from "@/core/models/roles";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
import { InternalBotModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { api } from "@/core/helpers/Api";

export interface AvatarSnapshot {
    avatar: string | null;
    previous_avatar: string | null;
    avatar_revision: number;
    can_restore: boolean;
}
interface Profile {
    uid: string;
    display_name: string;
    model_name: string;
}
interface ReviewedContext {
    source_revision: string;
    sources: { id: string; kind: string; text: string }[];
    profiles: Profile[];
}
export interface AvatarGenerationRequest {
    id: number;
    prompt: string;
    onGenerate: () => void;
}
interface BaseProps {
    base: string;
    canUpdate: boolean;
    disabled?: boolean;
    revisionKey?: string;
}
type Props = BaseProps &
    (
        | { onPersisted: (snapshot: AvatarSnapshot) => void; onDraftApplied?: never; generationRequest?: never }
        | { onPersisted?: never; onDraftApplied: (file: File) => void; generationRequest?: AvatarGenerationRequest }
    );
const instruction = [
    "Create a single symbolic icon. Flat vector style, strong silhouette, simple geometry, high contrast at 32–48px.",
    "Center with generous crop-safe margins. Use one or two motifs. No small text, photographic texture, trademarks or identifiable faces.",
].join(" ");
export default function AvatarGeneration({ base, canUpdate, disabled, onPersisted, onDraftApplied, generationRequest, revisionKey }: Props) {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser);
    const session = useAuthStore((state) => state.getSessionVersion());
    const [open, setOpen] = useState(false);
    const [activeRequest, setActiveRequest] = useState<AvatarGenerationRequest>();
    const [snapshot, setSnapshot] = useState<AvatarSnapshot | null>(null);
    const [profiles, setProfiles] = useState<Profile[]>([]);
    const [profileUID, setProfileUID] = useState("");
    const [prompt, setPrompt] = useState(instruction);
    const [context, setContext] = useState<ReviewedContext | null>(null);
    const [sourceIDs, setSourceIDs] = useState<string[]>([]);
    const [summaryProfileUID, setSummaryProfileUID] = useState("");
    const [textConsent, setTextConsent] = useState(false);
    const [semanticPrompt, setSemanticPrompt] = useState(false);
    const [consent, setConsent] = useState(false);
    const [restoreConsent, setRestoreConsent] = useState(false);
    const [preview, setPreview] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const request = useRef<AbortController | null>(null);
    const identity = [actor?.uid, base, canUpdate, disabled, open, session, profileUID, revisionKey, summaryProfileUID, ...sourceIDs].join(":");
    const current = useRef(identity);
    current.current = identity;
    const draft = !!onDraftApplied;
    const draftCallback = useRef(onDraftApplied);
    draftCallback.current = onDraftApplied;
    const callbacks = useRef(onPersisted);
    callbacks.current = onPersisted;
    const selectedModels = InternalBotModel.Model.useModels(
        (model) => model.uid === profileUID || model.uid === summaryProfileUID,
        [profileUID, summaryProfileUID]
    );
    const canConfigure =
        actor?.is_admin &&
        actor.setting_role_actions?.some((action) => action === ROLE_ALL_GRANTED || action === SettingRole.EAction.InternalBotUpdate);
    const selected = profiles.find((profile) => profile.uid === profileUID);
    const safe = (controller: AbortController, started: string) =>
        !controller.signal.aborted &&
        current.current === started &&
        useAuthStore.getState().currentUser === actor &&
        useAuthStore.getState().getSessionVersion() === session;
    useEffect(() => {
        request.current?.abort();
        setBusy(false);
        setPreview("");
        setConsent(false);
        setRestoreConsent(false);
        setTextConsent(false);
        setError("");
        return () => request.current?.abort();
    }, [identity, actor]);
    useEffect(() => {
        setPrompt(instruction);
        setActiveRequest(undefined);
        setSnapshot(null);
        setProfiles([]);
        setProfileUID("");
        setContext(null);
        setSourceIDs([]);
        setSummaryProfileUID("");
        setSemanticPrompt(false);
    }, [actor, base]);
    useEffect(() => {
        setContext(null);
        setSourceIDs([]);
        setSummaryProfileUID("");
        if (semanticPrompt) setPrompt(instruction);
        setSemanticPrompt(false);
    }, [actor, base, canUpdate, disabled, revisionKey, open, session]);
    const observedUIDs = useRef(new Set<string>());
    const observedSignature = selectedModels.map((model) => model.uid).join(",");
    useEffect(() => {
        const missing = [profileUID, summaryProfileUID].some(
            (uid) => uid && observedUIDs.current.has(uid) && !selectedModels.some((model) => model.uid === uid)
        );
        selectedModels.forEach((model) => observedUIDs.current.add(model.uid));
        if (missing) {
            request.current?.abort();
            setContext(null);
            setSourceIDs([]);
            setSummaryProfileUID("");
            setProfileUID("");
            setTextConsent(false);
            setConsent(false);
            setPreview("");
            setBusy(false);
            if (semanticPrompt) setPrompt(instruction);
            setSemanticPrompt(false);
            setError("conflict");
        }
    }, [observedSignature, profileUID, summaryProfileUID]);
    const handledRequest = useRef<number | undefined>(undefined);
    const startedRequest = useRef<number | undefined>(undefined);
    const startAssistant = () => {
        if (!activeRequest || startedRequest.current === activeRequest.id) return;
        startedRequest.current = activeRequest.id;
        activeRequest.onGenerate();
    };
    useEffect(() => {
        if (!generationRequest) {
            handledRequest.current = undefined;
            return;
        }
        if (handledRequest.current === generationRequest.id || !actor || !canUpdate || disabled) return;
        handledRequest.current = generationRequest.id;
        setActiveRequest(generationRequest);
        setPrompt(`${instruction} ${generationRequest.prompt}`.slice(0, 2000));
        setOpen(true);
    }, [generationRequest, actor, canUpdate, disabled]);
    const options = (signal: AbortSignal) => ({ signal, env: { secretInput: true, interceptToast: true } as never, validateStatus: () => true });
    const initialize = async () => {
        if (!actor || !canUpdate || disabled) return;
        const controller = new AbortController();
        request.current = controller;
        const started = identity;
        setBusy(true);
        setError("");
        try {
            const [avatar, catalog] = await Promise.all([
                draft
                    ? Promise.resolve({ status: 200, data: { avatar: null, previous_avatar: null, avatar_revision: 0, can_restore: false } })
                    : api.get(base, options(controller.signal)),
                api.get(`${base}/image-profiles`, options(controller.signal)),
            ]);
            if (!safe(controller, started)) return;
            if (avatar.status !== 200 || catalog.status !== 200) throw new Error();
            setSnapshot(avatar.data);
            setProfiles(catalog.data.profiles ?? []);
        } catch {
            if (safe(controller, started)) setError("failed");
        } finally {
            if (safe(controller, started)) setBusy(false);
        }
    };
    useEffect(() => {
        if (open) void initialize();
    }, [open, actor, base, canUpdate]);
    const operate = async (action: "context" | "semantic-preview" | "preview" | "apply" | "restore") => {
        if (!actor || !canUpdate || disabled || busy || !snapshot) return;
        if (
            action === "semantic-preview" &&
            (!context || !textConsent || !sourceIDs.length || !context.profiles.some((profile) => profile.uid === summaryProfileUID))
        )
            return;
        if (action === "preview" && (!consent || !selected || !prompt.trim())) return;
        if (action === "apply" && !preview) return;
        if (action === "restore" && (!restoreConsent || !snapshot.can_restore)) return;
        const controller = new AbortController();
        request.current?.abort();
        request.current = controller;
        const started = identity;
        setBusy(true);
        setError("");
        setConsent(false);
        setRestoreConsent(false);
        setTextConsent(false);
        try {
            let response;
            if (action === "context") response = await api.post(`${base}/context`, {}, options(controller.signal));
            else if (action === "semantic-preview") {
                response = await api.post(
                    `${base}/semantic-preview`,
                    { source_revision: context!.source_revision, profile_uid: summaryProfileUID, source_ids: sourceIDs, confirmed: true },
                    options(controller.signal)
                );
            } else if (action === "preview") {
                startAssistant();
                setPreview("");
                response = await api.post(
                    `${base}/preview`,
                    { profile_uid: profileUID, prompt: prompt.trim(), confirmed: true },
                    options(controller.signal)
                );
            } else if (action === "apply") {
                const encoded = preview.split(",")[1];
                const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
                const file = new File([bytes], "generated-avatar.png", { type: "image/png" });
                if (draft) {
                    if (safe(controller, started)) {
                        draftCallback.current?.(file);
                        setOpen(false);
                    }
                    return;
                }
                const form = new FormData();
                form.append("avatar", file);
                form.append("expected_revision", String(snapshot.avatar_revision));
                response = await api.put(base, form, options(controller.signal));
            } else response = await api.post(`${base}/restore`, { expected_revision: snapshot.avatar_revision }, options(controller.signal));
            if (!safe(controller, started)) return;
            if (response.status === 409) {
                const [fresh, catalog] = await Promise.all([
                    draft
                        ? Promise.resolve({ status: 200, data: { avatar: null, previous_avatar: null, avatar_revision: 0, can_restore: false } })
                        : api.get(base, options(controller.signal)),
                    api.get(`${base}/image-profiles`, options(controller.signal)),
                ]);
                if (!safe(controller, started)) return;
                if (fresh.status === 200) setSnapshot(fresh.data);
                setProfiles(catalog.status === 200 ? (catalog.data.profiles ?? []) : []);
                setPreview("");
                setContext(null);
                setSourceIDs([]);
                setSummaryProfileUID("");
                if (semanticPrompt) setPrompt(instruction);
                setSemanticPrompt(false);
                setError("conflict");
                return;
            }
            if (response.status !== 200) {
                setError(response.status === 429 ? "quota" : response.status === 504 ? "timeout" : "failed");
                return;
            }
            if (action === "preview") {
                const source = response.data.preview_data_url;
                if (typeof source !== "string" || source.length > 3_000_000 || !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(source))
                    throw new Error();
                setPreview(source);
            } else if (action === "context") {
                const data = response.data;
                if (
                    data.external_request !== false ||
                    typeof data.source_revision !== "string" ||
                    !Array.isArray(data.sources) ||
                    !Array.isArray(data.profiles)
                )
                    throw new Error();
                if (
                    data.sources.some(
                        (source: ReviewedContext["sources"][number]) =>
                            typeof source.id !== "string" || typeof source.kind !== "string" || typeof source.text !== "string"
                    )
                )
                    throw new Error();
                setContext(data);
                setSourceIDs(data.sources.map((source: ReviewedContext["sources"][number]) => source.id));
                setSummaryProfileUID("");
                setPrompt(instruction);
                setSemanticPrompt(false);
                setPreview("");
            } else if (action === "semantic-preview") {
                if (
                    response.data.external_request !== true ||
                    response.data.source_revision !== context!.source_revision ||
                    response.data.profile_uid !== summaryProfileUID ||
                    typeof response.data.prompt !== "string" ||
                    !response.data.prompt.trim() ||
                    response.data.prompt.length > 2000
                )
                    throw new Error();
                setPrompt(response.data.prompt);
                setSemanticPrompt(true);
                setPreview("");
            } else {
                const fresh = await api.get(base, options(controller.signal));
                if (!safe(controller, started)) return;
                if (
                    fresh.status !== 200 ||
                    fresh.data.avatar_revision !== response.data.avatar_revision ||
                    fresh.data.avatar !== response.data.avatar
                ) {
                    setError("readback");
                    return;
                }
                setSnapshot(fresh.data);
                setPreview("");
                callbacks.current?.({ ...fresh.data, avatar: fresh.data.avatar ? Utils.String.convertServerFileURL(fresh.data.avatar) : null });
            }
        } catch {
            if (safe(controller, started)) setError("failed");
        } finally {
            if (safe(controller, started)) setBusy(false);
        }
    };
    return (
        <>
            <Button
                type="button"
                variant="outline"
                disabled={!canUpdate || disabled || !actor}
                onClick={() => {
                    setActiveRequest(undefined);
                    setOpen(true);
                }}
            >
                {t("internalBot.avatar.generate")}
            </Button>
            <Dialog.Root open={open} onOpenChange={setOpen}>
                <Dialog.Content className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto" aria-describedby="">
                    {selectedModels.map((model) => (
                        <ProfileObserver
                            key={model.uid}
                            model={model}
                            invalidate={() => {
                                request.current?.abort();
                                setBusy(false);
                                setPreview("");
                                setConsent(false);
                                setProfiles([]);
                                setProfileUID("");
                                setContext(null);
                                setSourceIDs([]);
                                setSummaryProfileUID("");
                                setTextConsent(false);
                                if (semanticPrompt) setPrompt(instruction);
                                setSemanticPrompt(false);
                                setError("conflict");
                            }}
                        />
                    ))}
                    <Dialog.Header>
                        <Dialog.Title>{t("internalBot.avatar.title")}</Dialog.Title>
                    </Dialog.Header>
                    {!busy && profiles.length === 0 && (
                        <p>
                            {t("internalBot.avatar.unavailable")}
                            {canConfigure && (
                                <a className="ml-1 underline" href="/settings/internal-bots">
                                    {t("internalBot.avatar.settings")}
                                </a>
                            )}
                        </p>
                    )}
                    <label className="grid gap-1 text-sm">
                        {t("internalBot.avatar.profile")}
                        <select
                            className="h-9 rounded-md border bg-background px-2"
                            value={profileUID}
                            disabled={busy || !canUpdate}
                            onChange={(event) => setProfileUID(event.currentTarget.value)}
                        >
                            <option value="">{t("internalBot.avatar.choose")}</option>
                            {profiles.map((profile) => (
                                <option key={profile.uid} value={profile.uid}>
                                    {profile.display_name} · {profile.model_name}
                                </option>
                            ))}
                        </select>
                    </label>
                    {base.startsWith("/board/") && (
                        <Button type="button" variant="outline" disabled={busy || !canUpdate || !snapshot} onClick={() => void operate("context")}>
                            {t("internalBot.avatar.context")}
                        </Button>
                    )}
                    {context && (
                        <fieldset className="grid gap-3 rounded-md border p-3" disabled={busy || !canUpdate}>
                            <legend className="px-1 text-sm font-medium">{t("internalBot.avatar.review_sources")}</legend>
                            <p className="text-sm">{t("internalBot.avatar.source_warning")}</p>
                            {context.sources.map((source) => (
                                <label key={source.id} className="flex items-start gap-2 whitespace-pre-wrap break-words text-sm">
                                    <Checkbox
                                        checked={sourceIDs.includes(source.id)}
                                        onCheckedChange={(checked) => {
                                            setSourceIDs((ids) => (checked === true ? [...ids, source.id] : ids.filter((id) => id !== source.id)));
                                            setTextConsent(false);
                                            setConsent(false);
                                            setPreview("");
                                            if (semanticPrompt) setPrompt(instruction);
                                            setSemanticPrompt(false);
                                        }}
                                    />
                                    <span>{source.text}</span>
                                </label>
                            ))}
                            <label className="grid gap-1 text-sm">
                                {t("internalBot.avatar.summary_profile")}
                                <select
                                    className="h-9 rounded-md border bg-background px-2"
                                    value={summaryProfileUID}
                                    onChange={(event) => {
                                        setSummaryProfileUID(event.currentTarget.value);
                                        setTextConsent(false);
                                        setConsent(false);
                                        setPreview("");
                                        if (semanticPrompt) setPrompt(instruction);
                                        setSemanticPrompt(false);
                                    }}
                                >
                                    <option value="">{t("internalBot.avatar.choose")}</option>
                                    {context.profiles.map((profile) => (
                                        <option key={profile.uid} value={profile.uid}>
                                            {profile.display_name} · {profile.model_name}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            {!context.profiles.length && <p className="text-sm">{t("internalBot.avatar.summary_unavailable")}</p>}
                            <label className="flex gap-2 text-sm">
                                <Checkbox
                                    checked={textConsent}
                                    disabled={!summaryProfileUID || !sourceIDs.length}
                                    onCheckedChange={(checked) => setTextConsent(checked === true)}
                                />
                                {t("internalBot.avatar.text_consent")}
                            </label>
                            <Button
                                type="button"
                                disabled={busy || !canUpdate || !textConsent || !summaryProfileUID || !sourceIDs.length}
                                onClick={() => void operate("semantic-preview")}
                            >
                                {t("internalBot.avatar.summarize")}
                            </Button>
                        </fieldset>
                    )}
                    <label className="grid gap-1 text-sm">
                        {t("internalBot.avatar.prompt")}
                        <Textarea
                            value={prompt}
                            maxLength={2000}
                            disabled={busy || !canUpdate}
                            onChange={(event) => {
                                request.current?.abort();
                                setPrompt(event.currentTarget.value);
                                setSemanticPrompt(false);
                                setTextConsent(false);
                                setConsent(false);
                                setPreview("");
                            }}
                        />
                    </label>
                    {selected && (
                        <p className="text-sm">
                            {selected.display_name} · {selected.model_name}
                        </p>
                    )}
                    <label className="flex gap-2 text-sm">
                        <Checkbox checked={consent} disabled={busy || !canUpdate} onCheckedChange={(value) => setConsent(value === true)} />
                        {t("internalBot.image.consent")}
                    </label>
                    <Button
                        type="button"
                        disabled={busy || !canUpdate || !selected || !consent || !prompt.trim() || !snapshot}
                        onClick={() => void operate("preview")}
                    >
                        {t(activeRequest ? "bot.agent.Generate draft and avatar" : "internalBot.avatar.generate")}
                    </Button>
                    {activeRequest && (
                        <Button
                            type="button"
                            variant="outline"
                            disabled={busy || !canUpdate}
                            onClick={() => {
                                startAssistant();
                                setOpen(false);
                            }}
                        >
                            {t("bot.agent.Create text draft only")}
                        </Button>
                    )}
                    {preview && (
                        <>
                            <div className="flex flex-wrap items-end gap-3">
                                {[32, 48, 96].map((size) => (
                                    <figure key={size} className="space-y-1 text-center text-xs">
                                        <div className="flex gap-2">
                                            <img
                                                src={preview}
                                                width={size}
                                                height={size}
                                                alt={t("internalBot.image.preview")}
                                                className="rounded-full border object-contain"
                                            />
                                            <img
                                                src={preview}
                                                width={size}
                                                height={size}
                                                alt={t("internalBot.avatar.square")}
                                                className="rounded border object-contain"
                                            />
                                        </div>
                                        <figcaption>{size}px</figcaption>
                                    </figure>
                                ))}
                            </div>
                            <Button type="button" disabled={busy || !canUpdate} onClick={() => void operate("apply")}>
                                {t("internalBot.avatar.apply")}
                            </Button>
                        </>
                    )}
                    {snapshot?.can_restore && (
                        <>
                            <label className="flex gap-2 text-sm">
                                <Checkbox
                                    checked={restoreConsent}
                                    disabled={busy || !canUpdate}
                                    onCheckedChange={(value) => setRestoreConsent(value === true)}
                                />
                                {t("internalBot.avatar.restore_confirm")}
                            </label>
                            <Button
                                type="button"
                                variant="outline"
                                disabled={busy || !canUpdate || !restoreConsent}
                                onClick={() => void operate("restore")}
                            >
                                {t("internalBot.avatar.restore")}
                            </Button>
                        </>
                    )}
                    {error && (
                        <p role="alert" className="text-sm text-destructive">
                            {t(`internalBot.avatar.${error}`)}
                        </p>
                    )}
                    <Button
                        type="button"
                        variant="ghost"
                        onClick={() => {
                            request.current?.abort();
                            setBusy(false);
                            setConsent(false);
                            setTextConsent(false);
                            setPreview("");
                            if (!busy) setOpen(false);
                        }}
                    >
                        {t("common.Cancel")}
                    </Button>
                </Dialog.Content>
            </Dialog.Root>
        </>
    );
}

function ProfileObserver({ model, invalidate }: { model: InternalBotModel.TModel; invalidate: () => void }) {
    const value = model.useField("value");
    const updatedAt = model.useField("updated_at");
    const platform = model.useField("platform");
    const runningType = model.useField("platform_running_type");
    const botType = model.useField("bot_type");
    const name = model.useField("display_name");
    const signature = `${value}:${updatedAt?.getTime()}:${platform}:${runningType}:${botType}:${name}`;
    const previous = useRef(signature);
    useEffect(() => {
        if (previous.current !== signature) {
            previous.current = signature;
            invalidate();
        }
    }, [signature, invalidate]);
    return null;
}
