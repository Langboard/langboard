import { isAxiosError } from "axios";
import Button from "@/components/base/Button";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { api } from "@/core/helpers/Api";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
    assertResourceSelectionCurrent,
    confirmResourceWrite,
    ResourceSelectionConflict,
    type ResourceCandidate,
} from "@/core/apps/ResourceSelection";

interface Resource {
    resource_uid: string;
    resource_type: string;
    external_resource_id: string;
    selected: boolean;
    access_revision: number;
}
interface Snapshot {
    app_revision: string;
    binding_uid: string;
    binding_revision: string;
    resource_types: string[];
    items: Resource[];
    next_cursor: string | null;
}

interface InboundResourcesProps {
    app: CatalogApp;
    connectionUID: string;
    onBusy: (busy: boolean) => void;
}
export default function BoardSettingsInboundResources(props: InboundResourcesProps) {
    const { project, currentUser, canEditBasicInfo } = useBoardSettings();
    return <InboundResources {...props} projectUID={project.uid} userUID={currentUser.uid} canEditBasicInfo={canEditBasicInfo} />;
}

export function InboundResources({
    app,
    connectionUID,
    onBusy,
    projectUID,
    userUID,
    canEditBasicInfo,
    suggestions,
    authority,
}: InboundResourcesProps & {
    projectUID: string;
    userUID: string;
    canEditBasicInfo: boolean;
    suggestions?: ResourceCandidate[];
    authority?: {
        app_revision: string;
        binding_revision: string;
        signal: AbortSignal;
        revalidate: (signal: AbortSignal) => Promise<void>;
        onDenied: () => void;
        isCurrent: () => boolean;
    };
}) {
    const [t] = useTranslation();
    const liveScope = useRef("");
    liveScope.current = JSON.stringify([projectUID, userUID, app.key, connectionUID, app.app_revision, app.binding?.revision]);
    const lifetime = useRef(new AbortController());
    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        return () => controller.abort();
    }, [projectUID, userUID, app.key, connectionUID]);
    const mayWrite = useRef(canEditBasicInfo);
    mayWrite.current = canEditBasicInfo;
    const [cursor, setCursor] = useState<string>();
    const [type, setType] = useState("");
    const [externalID, setExternalID] = useState("");
    const [confirm, setConfirm] = useState<Resource | "add" | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef({ busy: false, needsRefresh: false });
    const path = `/board/${projectUID}/settings/apps/${encodeURIComponent(app.key)}/inbound-connections/${connectionUID}/resources`;
    const config = { env: { interceptToast: true } as never };
    const list = useQuery({
        queryKey: ["inbound-resources", userUID, projectUID, app.key, connectionUID, cursor],
        queryFn: async ({ signal }) =>
            (
                await api.get<Snapshot>(path, {
                    ...config,
                    signal: AbortSignal.any([signal, lifetime.current.signal, ...(authority ? [authority.signal] : [])]),
                    params: { after: cursor },
                })
            ).data,
        retry: 0,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const busy = pending || list.isFetching;
    useEffect(() => {
        if (authority && isAxiosError(list.error) && [401, 403, 404, 409].includes(list.error.response?.status ?? 0)) authority.onDenied();
    }, [authority, list.error]);
    useEffect(() => {
        onBusy(pending);
        return () => onBusy(false);
    }, [pending, onBusy]);
    const refresh = async () => {
        setPending(true);
        try {
            const result = await list.refetch();
            setFailed(result.isError);
            if (!result.isError) {
                setConfirm(null);
                writing.current.needsRefresh = false;
            }
        } catch {
            setFailed(true);
        } finally {
            setPending(false);
        }
    };
    const save = async () => {
        const data = list.data;
        if (!data || !confirm || !canEditBasicInfo || busy || failed || list.isError || writing.current.busy || writing.current.needsRefresh) return;
        const selected = confirm === "add" || !confirm.selected;
        if (selected && app.is_available === false) return;
        const identity = confirm === "add" ? { resource_type: type, external_resource_id: externalID.trim() } : confirm;
        if (!identity.resource_type || !identity.external_resource_id) return;
        const submittedScope = liveScope.current;
        setPending(true);
        try {
            const signal = AbortSignal.any([lifetime.current.signal, ...(authority ? [authority.signal] : [])]);
            await confirmResourceWrite(
                writing.current,
                signal,
                async () => {
                    await authority?.revalidate(signal);
                    const fresh = (await api.get<Snapshot>(path, { ...config, signal, params: { after: cursor } })).data;
                    signal.throwIfAborted();
                    if (!mayWrite.current) throw new ResourceSelectionConflict("Board authority changed");
                    assertResourceSelectionCurrent(data, fresh, identity, authority, selected);
                },
                () =>
                    api.put(
                        path,
                        {
                            app_revision: data.app_revision,
                            binding_uid: data.binding_uid,
                            expected_revision: data.binding_revision,
                            resource_type: identity.resource_type,
                            external_resource_id: identity.external_resource_id,
                            selected,
                            expected_access_revision: confirm === "add" ? null : confirm.access_revision,
                        },
                        { ...config, signal }
                    ),
                () => liveScope.current === submittedScope && mayWrite.current && (authority?.isCurrent() ?? true)
            );
            setConfirm(null);
            setExternalID("");
            const result = await list.refetch();
            setFailed(result.isError);
        } catch (error) {
            setFailed(true);
            if (
                authority &&
                (error instanceof ResourceSelectionConflict || (isAxiosError(error) && [401, 403, 404, 409].includes(error.response?.status ?? 0)))
            )
                authority.onDenied();
        } finally {
            setPending(false);
        }
    };
    return (
        <section className="space-y-3 rounded-lg border p-4" aria-busy={busy}>
            <h4 className="font-semibold">{t("project.settings.Manage service resources")}</h4>
            <p className="text-sm text-muted-foreground">{t("project.settings.Service resource help")}</p>
            {(failed || list.isError) && <p role="alert">{t("project.settings.Inbound connection refresh required")}</p>}
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void refresh()}>
                {t("common.Retry")}
            </Button>
            {list.isFetching && <p role="status">{t("common.Loading...")}</p>}
            {suggestions && (
                <ul className="space-y-2" aria-label={app.name}>
                    {suggestions.map((candidate) => {
                        const existing = list.data?.items.find(
                            (item) => item.resource_type === candidate.resource_type && item.external_resource_id === candidate.external_resource_id
                        );
                        return (
                            <li key={JSON.stringify([candidate.resource_type, candidate.external_resource_id])}>
                                <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={
                                        !canEditBasicInfo ||
                                        busy ||
                                        failed ||
                                        list.isError ||
                                        !!confirm ||
                                        existing?.selected ||
                                        !list.data?.resource_types.includes(candidate.resource_type)
                                    }
                                    onClick={() => {
                                        setType(candidate.resource_type);
                                        setExternalID(candidate.external_resource_id);
                                        setConfirm(existing ?? "add");
                                    }}
                                >
                                    {candidate.label} · {candidate.resource_type} · {candidate.external_resource_id}
                                </Button>
                            </li>
                        );
                    })}
                </ul>
            )}
            <ul className="space-y-2">
                {list.data?.items.map((resource) => (
                    <li key={resource.resource_uid} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm">
                        <span className="min-w-0 break-all">
                            {resource.resource_type} · {resource.external_resource_id}
                        </span>
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={
                                !canEditBasicInfo || busy || failed || list.isError || !!confirm || (!resource.selected && app.is_available === false)
                            }
                            onClick={() => setConfirm(resource)}
                        >
                            {t(resource.selected ? "project.settings.Remove service resource" : "project.settings.Select service resource")}
                        </Button>
                    </li>
                ))}
            </ul>
            <label className="block space-y-1 text-sm">
                <span>{t("project.settings.Service resource type")}</span>
                <select
                    aria-label={t("project.settings.Service resource type")}
                    className="h-10 w-full rounded-md border bg-background px-3"
                    value={type}
                    disabled={busy || failed || !!confirm}
                    onChange={(e) => setType(e.target.value)}
                >
                    <option value="">{t("project.settings.Service resource type")}</option>
                    {list.data?.resource_types.map((value) => (
                        <option key={value} value={value}>
                            {value}
                        </option>
                    ))}
                </select>
            </label>
            <label className="block space-y-1 text-sm">
                <span>{t("project.settings.Service resource ID")}</span>
                <input
                    className="h-10 w-full rounded-md border bg-background px-3"
                    value={externalID}
                    maxLength={200}
                    disabled={busy || failed || !!confirm}
                    onChange={(e) => setExternalID(e.target.value)}
                />
            </label>
            <Button
                size="sm"
                disabled={
                    !canEditBasicInfo || busy || failed || list.isError || !!confirm || !type || !externalID.trim() || app.is_available === false
                }
                onClick={() => setConfirm("add")}
            >
                {t("project.settings.Select service resource")}
            </Button>
            {confirm && (
                <div className="space-y-2 rounded-md border p-3">
                    <p className="break-all text-sm">
                        {confirm === "add" ? `${type} · ${externalID.trim()}` : `${confirm.resource_type} · ${confirm.external_resource_id}`}
                    </p>
                    <div className="flex gap-2">
                        <Button size="sm" disabled={busy || failed || list.isError} onClick={() => void save()}>
                            {t("project.settings.Confirm inbound change")}
                        </Button>
                        <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>
                            {t("common.Cancel")}
                        </Button>
                    </div>
                </div>
            )}
            <div className="flex gap-2">
                {list.data?.next_cursor && (
                    <Button size="sm" variant="ghost" disabled={busy || failed || !!confirm} onClick={() => setCursor(list.data!.next_cursor!)}>
                        {t("project.settings.More inbound connections")}
                    </Button>
                )}
                {cursor && (
                    <Button size="sm" variant="ghost" disabled={busy || failed || !!confirm} onClick={() => setCursor(undefined)}>
                        {t("project.settings.First inbound connections")}
                    </Button>
                )}
            </div>
        </section>
    );
}
