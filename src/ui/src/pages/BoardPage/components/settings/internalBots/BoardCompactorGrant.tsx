import Alert from "@/components/base/Alert";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import { api } from "@/core/helpers/Api";
import { InternalBotModel } from "@/core/models";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface Permissions {
    can_read: boolean;
    can_compact: boolean;
    can_archive: boolean;
}
interface Grant extends Permissions {
    project_uid: string;
    profile_uid: string;
}
interface GrantFormProps {
    projectUID: string;
    profileUID: string;
    displayName: string;
}

function readGrant(value: Grant, projectUID: string, profileUID: string): Permissions {
    if (
        value?.project_uid !== projectUID ||
        value.profile_uid !== profileUID ||
        [value.can_read, value.can_compact, value.can_archive].some((item) => typeof item !== "boolean") ||
        (!value.can_read && (value.can_compact || value.can_archive))
    )
        throw new Error("Invalid grant response");
    return { can_read: value.can_read, can_compact: value.can_compact, can_archive: value.can_archive };
}

export function BoardCompactorGrantForm({ projectUID, profileUID, displayName }: GrantFormProps) {
    const [t] = useTranslation();
    const [draft, setDraft] = useState<Permissions | null>(null);
    const [state, setState] = useState<"loading" | "ready" | "saving" | "saved" | "error">("loading");
    const [reload, setReload] = useState(0);
    const request = useRef<AbortController | null>(null);
    const endpoint = `/board/${encodeURIComponent(projectUID)}/compactor/${encodeURIComponent(profileUID)}/grant`;

    useEffect(() => {
        const controller = new AbortController();
        request.current = controller;
        setDraft(null);
        setState("loading");
        void api
            .get<Grant>(endpoint, { signal: controller.signal })
            .then(({ data }) => {
                if (controller.signal.aborted) return;
                setDraft(readGrant(data, projectUID, profileUID));
                setState("ready");
            })
            .catch(() => {
                if (!controller.signal.aborted) setState("error");
            });
        return () => {
            request.current?.abort();
            controller.abort();
        };
    }, [endpoint, projectUID, profileUID, reload]);

    const save = async () => {
        if (!draft || state === "saving") return;
        const controller = new AbortController();
        request.current?.abort();
        request.current = controller;
        setState("saving");
        try {
            await api.put(endpoint, draft, { signal: controller.signal });
            // Read current authority again; a successful PUT alone is not a saved UI snapshot.
            const { data } = await api.get<Grant>(endpoint, { signal: controller.signal });
            if (controller.signal.aborted) return;
            setDraft(readGrant(data, projectUID, profileUID));
            setState("saved");
        } catch {
            if (controller.signal.aborted) return;
            setDraft(null);
            setState("error");
        }
    };
    const labels: Record<keyof Permissions, string> = {
        can_read: "Read board cards",
        can_compact: "Compact comments",
        can_archive: "Archive cards",
    };
    return (
        <fieldset className="min-w-0 space-y-3 rounded-xl border p-4" disabled={state === "saving"}>
            <legend className="max-w-full break-words px-1 text-sm font-semibold">{displayName}</legend>
            <p className="text-sm text-muted-foreground">{t("project.settings.Compactor authority help")}</p>
            {state === "loading" && <p role="status">{t("common.Loading...")}</p>}
            {state === "error" && (
                <Alert variant="destructive" title={t("project.settings.Compactor authority unavailable")}>
                    <Button size="sm" variant="outline" onClick={() => setReload((value) => value + 1)}>
                        {t("common.Retry")}
                    </Button>
                </Alert>
            )}
            {draft && state !== "error" && (
                <>
                    {(["can_read", "can_compact", "can_archive"] as const).map((permission) => (
                        <label key={permission} className="flex items-start gap-2 text-sm">
                            <Checkbox
                                checked={draft[permission]}
                                disabled={state === "saving" || (permission !== "can_read" && !draft.can_read)}
                                onCheckedChange={(checked) => {
                                    if (typeof checked !== "boolean") return;
                                    setDraft(
                                        (current) =>
                                            current && {
                                                ...current,
                                                [permission]: checked,
                                                ...(permission === "can_read" && !checked ? { can_compact: false, can_archive: false } : {}),
                                            }
                                    );
                                    setState("ready");
                                }}
                            />
                            <span>{t(`project.settings.${labels[permission]}`)}</span>
                        </label>
                    ))}
                    <p className="text-sm text-muted-foreground">{t("project.settings.Compactor runtime pending")}</p>
                    <Button size="sm" disabled={state === "saving" || state === "saved"} onClick={() => void save()}>
                        {t("common.Save")}
                    </Button>
                    {state === "saved" && (
                        <p role="status" className="text-sm">
                            {t("project.settings.Compactor authority saved")}
                        </p>
                    )}
                </>
            )}
        </fieldset>
    );
}

export default function BoardCompactorGrant() {
    const [t] = useTranslation();
    const { project, currentUser } = useBoardSettings();
    const actor = useAuthStore((store) => store.currentUser);
    const isAdmin = currentUser.useField("is_admin");
    const ownerUID = project.useField("owner_uid");
    const profiles = InternalBotModel.Model.useModels((model) => model.bot_type === InternalBotModel.EInternalBotType.CommentCompaction);
    if (!actor || actor.uid !== currentUser.uid || (!isAdmin && actor.uid !== ownerUID)) return null;
    return (
        <section aria-label={t("project.settings.Compactor board authority")} className="space-y-3">
            <h3 className="font-semibold">{t("project.settings.Compactor board authority")}</h3>
            {!profiles.length && <p className="text-sm text-muted-foreground">{t("project.settings.Compactor profile missing")}</p>}
            {profiles.map((profile) => (
                <BoardCompactorGrantForm
                    key={`${actor.uid}:${project.uid}:${profile.uid}`}
                    projectUID={project.uid}
                    profileUID={profile.uid}
                    displayName={profile.display_name}
                />
            ))}
        </section>
    );
}
