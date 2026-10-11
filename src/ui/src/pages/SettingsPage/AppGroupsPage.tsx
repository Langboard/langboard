import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import type { AuthUser } from "@/core/models";
import type { AppComposition } from "@/controllers/api/board/useBoardAppCatalog";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import SettingsLoadState from "./SettingsLoadState";

type Draft = Pick<AppComposition, "key" | "name" | "description" | "module_keys"> & { expected_revision: string | null };
const empty = (): Draft => ({ key: "", name: "", description: "", module_keys: [], expected_revision: null });
const root = "/settings/apps/compositions";

export default function AppGroupsPage({ currentUser }: { currentUser: AuthUser.TModel }) {
    const [t] = useTranslation();
    const query = useQuery({
        queryKey: ["app-compositions-admin", currentUser.uid],
        enabled: currentUser.is_admin,
        queryFn: async ({ signal }) => {
            const result = (
                await api.get<{
                    compositions: AppComposition[];
                    modules: { key: string; name: string; version: string | null; is_enabled: boolean; builtin: boolean }[];
                }>(root, { signal, env: { interceptToast: true } as never })
            ).data;
            if (!Array.isArray(result.compositions) || !Array.isArray(result.modules)) throw new Error("App grouping catalog unavailable");
            return result;
        },
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const [draft, setDraft] = useState<Draft | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const [confirmUngroup, setConfirmUngroup] = useState(false);
    const [saved, setSaved] = useState(false);
    if (!currentUser.is_admin) return null;
    const reload = async () => {
        const result = await query.refetch();
        if (!result.isError) {
            setDraft(null);
            setFailed(false);
            setConfirmUngroup(false);
        }
    };
    const save = async (ungroup = false) => {
        if (!draft || pending || failed || query.isError) return;
        setPending(true);
        setSaved(false);
        try {
            if (ungroup) await api.post(`${root}/${encodeURIComponent(draft.key)}/ungroup`, { expected_revision: draft.expected_revision });
            else await api.post(root, draft);
            setDraft(null);
            setConfirmUngroup(false);
            const result = await query.refetch();
            if (result.isError) setFailed(true);
            else setSaved(true);
        } catch {
            setFailed(true);
        } finally {
            setPending(false);
        }
    };
    const occupied = new Set(query.data?.compositions.filter((group) => group.key !== draft?.key).flatMap((group) => group.module_keys));
    return (
        <div className="mx-auto w-full max-w-3xl space-y-4 p-4 sm:p-6">
            <h2 className="text-xl font-semibold">{t("settings.App groups")}</h2>
            <p className="text-sm text-muted-foreground">{t("settings.App groups help")}</p>
            {!query.data && <SettingsLoadState error={query.isError} isFetching={query.isFetching} retry={() => void reload()} />}
            {(failed || (query.isError && query.data)) && (
                <div role="alert" className="rounded-lg border p-3">
                    <p>{t("settings.App groups conflict")}</p>
                    <Button size="sm" variant="outline" disabled={pending || query.isFetching} onClick={() => void reload()}>
                        {t("settings.App groups reload")}
                    </Button>
                </div>
            )}
            {saved && <p role="status">{t("settings.App groups saved")}</p>}
            {query.data && (
                <>
                    {!draft ? (
                        <>
                            <Button
                                size="sm"
                                disabled={pending || failed || query.isError}
                                onClick={() => {
                                    setDraft(empty());
                                    setSaved(false);
                                }}
                            >
                                {t("settings.App group create")}
                            </Button>
                            {query.data.compositions.map((group) => (
                                <article key={group.key} className="card card-border gap-3 border p-4">
                                    <h3 className="font-semibold">{group.name}</h3>
                                    <p className="text-sm text-muted-foreground">{group.description}</p>
                                    <p className="break-words text-xs text-muted-foreground">{group.module_keys.join(" · ")}</p>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={pending || failed || query.isError}
                                        onClick={() => {
                                            setDraft({
                                                key: group.key,
                                                name: group.name,
                                                description: group.description,
                                                module_keys: [...group.module_keys],
                                                expected_revision: group.revision,
                                            });
                                            setSaved(false);
                                        }}
                                    >
                                        {t("common.Edit")}
                                    </Button>
                                </article>
                            ))}
                        </>
                    ) : (
                        <fieldset disabled={pending} className="fieldset gap-3 rounded-lg border p-4">
                            <legend className="fieldset-legend">{t("settings.App groups")}</legend>
                            <label className="flex flex-col gap-1">
                                {t("settings.App group key")}
                                <input
                                    className="input w-full"
                                    value={draft.key}
                                    disabled={draft.expected_revision !== null}
                                    maxLength={32}
                                    onChange={(event) => setDraft({ ...draft, key: event.target.value })}
                                />
                            </label>
                            <label className="flex flex-col gap-1">
                                {t("settings.App group name")}
                                <input
                                    className="input w-full"
                                    value={draft.name}
                                    maxLength={80}
                                    onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                                />
                            </label>
                            <label className="flex flex-col gap-1">
                                {t("settings.App group description")}
                                <textarea
                                    className="textarea w-full"
                                    value={draft.description}
                                    maxLength={1000}
                                    onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                                />
                            </label>
                            <p className="text-sm font-medium">{t("settings.App group modules")}</p>
                            {query.data.modules.map((module) => (
                                <label key={module.key} className="flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        className="checkbox checkbox-sm"
                                        checked={draft.module_keys.includes(module.key)}
                                        disabled={
                                            occupied.has(module.key) || (!draft.module_keys.includes(module.key) && draft.module_keys.length >= 16)
                                        }
                                        onChange={(event) =>
                                            setDraft({
                                                ...draft,
                                                module_keys: event.target.checked
                                                    ? [...draft.module_keys, module.key]
                                                    : draft.module_keys.filter((key) => key !== module.key),
                                            })
                                        }
                                    />
                                    <span className="min-w-0 break-words">
                                        {module.name}{" "}
                                        <span className="text-xs text-muted-foreground">
                                            {module.key} · {module.version ?? "—"}
                                            {!module.is_enabled && <> · {t("common.Disabled")}</>}
                                        </span>
                                    </span>
                                </label>
                            ))}
                            <div className="flex flex-wrap gap-2">
                                <Button
                                    size="sm"
                                    disabled={
                                        failed ||
                                        query.isError ||
                                        !/^[a-z][a-z0-9_-]{0,31}$/.test(draft.key) ||
                                        !draft.name.trim() ||
                                        !draft.description.trim() ||
                                        !draft.module_keys.length
                                    }
                                    onClick={() => void save()}
                                >
                                    {t("common.Save")}
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => {
                                        setDraft(null);
                                        setConfirmUngroup(false);
                                    }}
                                >
                                    {t("common.Cancel")}
                                </Button>
                                {draft.expected_revision && (
                                    <Button size="sm" variant="outline" disabled={failed || query.isError} onClick={() => setConfirmUngroup(true)}>
                                        {t("settings.App group ungroup")}
                                    </Button>
                                )}
                            </div>
                            {confirmUngroup && (
                                <div className="space-y-2 rounded-md border p-3">
                                    <p>{t("settings.App group ungroup help")}</p>
                                    <Button size="sm" disabled={failed || query.isError} onClick={() => void save(true)}>
                                        {t("settings.App group confirm ungroup")}
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={() => setConfirmUngroup(false)}>
                                        {t("common.Cancel")}
                                    </Button>
                                </div>
                            )}
                        </fieldset>
                    )}
                </>
            )}
        </div>
    );
}
