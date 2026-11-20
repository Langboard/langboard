import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { api } from "@/core/helpers/Api";
import { assertResourceSelectionCurrent, confirmResourceWrite, ResourceSelectionConflict } from "@/core/apps/ResourceSelection";
import {
    devtronTarget,
    readDevtronResources,
    type DevtronAccounts,
    type DevtronConnection,
    type DevtronResource,
    type DevtronResources as ResourcePage,
    type DevtronScope,
} from "@/core/apps/DevtronSettings";
import DevtronScopeFields from "./DevtronScopeFields";

type Row = DevtronResource & { after?: string };
type Confirmation = {
    action: "select" | "remove" | "verify";
    id: string;
    row?: Row;
    shown: ResourcePage;
    helm?: string;
};
const selectionSnapshot = (page: ResourcePage) => ({
    ...page,
    items: page.items.map((row) => ({
        ...row,
        selected: row.is_selected,
        access_revision: row.revision,
    })),
});

export default function DevtronResources({
    app,
    projectUID,
    connection,
    metadata,
    guard,
    disabled,
    onBusy,
    onStatusChange,
    onRefresh,
    onRequireRefresh,
}: {
    app: CatalogApp;
    projectUID: string;
    connection: DevtronConnection;
    metadata: DevtronAccounts;
    guard: { busy: boolean; needsRefresh: boolean };
    disabled: boolean;
    onBusy: (busy: boolean) => void;
    onStatusChange?: () => void;
    onRefresh: () => void;
    onRequireRefresh: () => void;
}) {
    const [t] = useTranslation();
    const root = `/board/${projectUID}/settings/apps/devtron-panel/connections/${connection.connection_uid}`;
    const path = `/board/${projectUID}/apps/devtron-panel/connections/${connection.connection_uid}/resources`;
    const context = JSON.stringify([projectUID, connection.connection_uid, app.app_revision]);
    const current = useRef(context);
    current.current = context;
    const lifetime = useRef(new AbortController());
    const [data, setData] = useState<ResourcePage | null>(null);
    const [rows, setRows] = useState<Row[]>([]);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const [notice, setNotice] = useState("");
    const [target, setTarget] = useState<DevtronScope>({
        kind: "native",
        first: "",
        second: "",
    });
    const [confirm, setConfirm] = useState<Confirmation | null>(null);
    const [tuples, setTuples] = useState<Record<string, string>>({});
    const [search, setSearch] = useState("");
    const [pageNumber, setPageNumber] = useState("1");
    const [candidates, setCandidates] = useState<
        {
            appId: number;
            appName: string;
            environmentId: number;
            environmentName: string;
        }[]
    >([]);
    const config = { env: { interceptToast: true } as never };
    const read = async (signal: AbortSignal, after?: string) =>
        readDevtronResources(
            (
                await api.get<ResourcePage>(path, {
                    ...config,
                    signal,
                    params: { after },
                })
            ).data
        );
    const load = async (signal: AbortSignal, after?: string) => {
        const submitted = context;
        setPending(true);
        try {
            const fresh = await read(signal, after);
            if (signal.aborted || current.current !== submitted) return;
            if (
                after &&
                (!data ||
                    data.app_revision !== fresh.app_revision ||
                    data.binding_revision !== fresh.binding_revision ||
                    data.identity_revision !== fresh.identity_revision)
            )
                throw new ResourceSelectionConflict("Resource page changed");
            setRows((old) =>
                after
                    ? [...new Map([...old, ...fresh.items.map((row) => ({ ...row, after }))].map((row) => [row.resource_uid, row])).values()]
                    : fresh.items
            );
            setData(fresh);
            setFailed(false);
        } catch {
            if (!signal.aborted && current.current === submitted) {
                setFailed(true);
                setData(null);
                setRows([]);
            }
        } finally {
            if (!signal.aborted && current.current === submitted) setPending(false);
        }
    };
    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        setData(null);
        setRows([]);
        setCandidates([]);
        setConfirm(null);
        setNotice("");
        void load(controller.signal);
        return () => controller.abort();
    }, [context]);
    useEffect(() => {
        onBusy(pending);
        return () => onBusy(false);
    }, [pending, onBusy]);
    const mayRead =
        metadata.available &&
        metadata.binding?.state === "enabled" &&
        metadata.binding.granted_capabilities.includes("resources.read") &&
        connection.state === "connected";
    const review = async (action: Confirmation["action"], id?: string, row?: Row) => {
        if (!data || pending || disabled || guard.busy || guard.needsRefresh || failed) return;
        const submitted = context,
            signal = lifetime.current.signal;
        setPending(true);
        setNotice("");
        try {
            const candidate = id ? { external_id: id, helm_app_id: undefined as string | undefined } : await devtronTarget(target);
            if (!candidate.external_id) throw new Error("A deployment identity is required");
            if (action === "verify" && candidate.external_id.startsWith("helm-release-")) {
                const external = await devtronTarget({
                    kind: "external",
                    first: tuples[candidate.external_id] ?? "",
                    second: "",
                });
                if (external.external_id !== candidate.external_id) throw new Error("Helm identity mismatch");
                candidate.helm_app_id = external.helm_app_id;
            }
            if (signal.aborted || current.current !== submitted) return;
            if (candidate.helm_app_id)
                setTuples((old) => ({
                    ...old,
                    [candidate.external_id!]: candidate.helm_app_id!,
                }));
            setConfirm({
                action,
                id: candidate.external_id,
                row: row ?? rows.find((item) => item.external_resource_id === candidate.external_id),
                shown: { ...data, items: rows },
                helm: candidate.helm_app_id,
            });
        } catch {
            if (!signal.aborted && current.current === submitted) setNotice(t("project.settings.devtron.invalidTarget"));
        } finally {
            if (!signal.aborted && current.current === submitted) setPending(false);
        }
    };
    const save = async () => {
        if (!confirm || !data || disabled || pending || guard.busy || guard.needsRefresh || failed) return;
        const request = confirm,
            signal = lifetime.current.signal,
            submitted = context;
        const valid = () => !signal.aborted && current.current === submitted;
        setPending(true);
        setNotice("");
        try {
            await confirmResourceWrite(
                guard,
                signal,
                async () => {
                    const fresh = await read(signal, request.row?.after);
                    if (fresh.identity_revision !== request.shown.identity_revision)
                        throw new ResourceSelectionConflict("Connection identity changed");
                    assertResourceSelectionCurrent(
                        selectionSnapshot(request.shown),
                        selectionSnapshot(fresh),
                        { resource_type: "application", external_resource_id: request.id },
                        undefined,
                        request.action !== "remove"
                    );
                },
                async () => {
                    if (request.action === "verify") {
                        if (!request.row) throw new Error("Existing resource is required");
                        const result = (
                            await api.post<{
                                ok: boolean;
                                resource: {
                                    resource_uid: string;
                                    access_revision: number;
                                    access_state: string;
                                    health: string;
                                };
                                error?: { status?: number };
                            }>(
                                `${root}/resources/${request.row.resource_uid}/verify`,
                                {
                                    app_revision: request.shown.app_revision,
                                    binding_revision: request.shown.binding_revision,
                                    access_revision: request.row.revision,
                                    helm_app_id: request.helm,
                                },
                                { ...config, signal }
                            )
                        ).data;
                        if (
                            !result.resource ||
                            result.resource.resource_uid !== request.row.resource_uid ||
                            result.resource.access_revision !== request.row.revision + 1 ||
                            (result.ok && (result.resource.access_state !== "granted" || result.resource.health !== "unknown")) ||
                            (!result.ok && !["denied", "unknown"].includes(result.resource.access_state))
                        )
                            throw new Error("Invalid verification receipt");
                        if (valid())
                            setNotice(
                                result.ok
                                    ? t("project.settings.devtron.verified")
                                    : t("project.settings.devtron.providerError", {
                                          status: result.error?.status ?? "—",
                                      })
                            );
                    } else {
                        const selected = request.action === "select";
                        const result = (
                            await api.put<DevtronResource>(
                                path,
                                {
                                    resource_type: "application",
                                    external_resource_id: request.id,
                                    selected,
                                    expected_revision: request.row?.revision ?? 0,
                                    expected_app_revision: request.shown.app_revision,
                                    expected_binding_revision: request.shown.binding_revision,
                                    expected_identity_revision: request.shown.identity_revision,
                                },
                                { ...config, signal }
                            )
                        ).data;
                        if (
                            result.external_resource_id !== request.id ||
                            result.is_selected !== selected ||
                            result.revision !== (request.row?.revision ?? 0) + 1
                        )
                            throw new Error("Invalid selection receipt");
                    }
                },
                valid
            );
            if (!valid()) return;
            setConfirm(null);
            const fresh = await read(signal);
            if (!valid()) return;
            setData(fresh);
            setRows(fresh.items);
            onStatusChange?.();
        } catch {
            if (valid()) {
                guard.needsRefresh = true;
                setFailed(true);
                setData(null);
                setRows([]);
                setCandidates([]);
                setConfirm(null);
                onRequireRefresh();
            }
        } finally {
            if (valid()) setPending(false);
        }
    };
    const discover = async () => {
        if (pending || disabled || guard.busy || guard.needsRefresh || !mayRead || !data) return;
        const signal = lifetime.current.signal,
            submitted = context;
        guard.busy = true;
        setPending(true);
        setNotice("");
        setCandidates([]);
        try {
            const offset = (Number(pageNumber) - 1) * 50;
            if (!/^[1-9][0-9]*$/.test(pageNumber) || !Number.isSafeInteger(offset) || offset > 1_000_000_000)
                throw new Error("Invalid inventory page");
            const result = (
                await api.post<{ resources: typeof candidates } | { ok: false; error: { status?: number } }>(
                    `${root}/discover`,
                    { app_revision: data.app_revision, search, offset, size: 50 },
                    { ...config, signal }
                )
            ).data;
            if (signal.aborted || current.current !== submitted) return;
            if ("ok" in result && result.ok === false)
                setNotice(
                    t("project.settings.devtron.providerError", {
                        status: result.error.status ?? "—",
                    })
                );
            else if ("resources" in result && Array.isArray(result.resources) && result.resources.length <= 250) setCandidates(result.resources);
            else throw new Error("Invalid discovery receipt");
        } catch {
            if (!signal.aborted && current.current === submitted) {
                setFailed(true);
                setData(null);
                onRequireRefresh();
            }
        } finally {
            guard.busy = false;
            if (!signal.aborted && current.current === submitted) setPending(false);
        }
    };
    const blocked = disabled || pending || failed || !data;
    return (
        <section className="flex min-w-0 flex-col gap-3 rounded-md border p-3" aria-busy={pending}>
            <h5 className="font-semibold">{t("project.settings.devtron.resources")}</h5>
            {failed && <p role="alert">{t("project.settings.devtron.refreshRequired")}</p>}
            {failed && (
                <Button size="sm" variant="outline" disabled={pending} onClick={onRefresh}>
                    {t("project.settings.devtron.refresh")}
                </Button>
            )}
            {notice && <p role="status">{notice}</p>}
            {!mayRead && <p className="text-sm text-muted-foreground">{t("project.settings.devtron.permissionsHelp")}</p>}
            <fieldset className="fieldset flex min-w-0 flex-col gap-2" disabled={blocked || !!confirm || !mayRead}>
                <legend className="fieldset-legend text-sm">{t("project.settings.devtron.discovery")}</legend>
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t("project.settings.devtron.search")}
                    <Input maxLength={200} value={search} onChange={(event) => setSearch(event.target.value)} />
                </label>
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t("project.settings.devtron.page")}
                    <Input type="number" min={1} step={1} value={pageNumber} onChange={(event) => setPageNumber(event.target.value)} />
                </label>
                <Button size="sm" variant="outline" onClick={() => void discover()}>
                    {t("project.settings.devtron.loadInventory")}
                </Button>
                {candidates.map((candidate) => (
                    <Button
                        key={`${candidate.appId}:${candidate.environmentId}`}
                        size="sm"
                        variant="outline"
                        disabled={
                            !!data?.next_cursor ||
                            rows.some(
                                (row) => row.external_resource_id === `app-${candidate.appId}-env-${candidate.environmentId}` && row.is_selected
                            )
                        }
                        onClick={() => void review("select", `app-${candidate.appId}-env-${candidate.environmentId}`)}
                    >
                        {candidate.appName} / {candidate.environmentName}
                    </Button>
                ))}
                <DevtronScopeFields value={target} onChange={setTarget} />
                <Button
                    size="sm"
                    variant="outline"
                    disabled={!!data?.next_cursor || !data?.resource_types.includes("application")}
                    onClick={() => void review("select")}
                >
                    {t("project.settings.devtron.reviewSelection")}
                </Button>
            </fieldset>
            <ul className="flex min-w-0 flex-col gap-2">
                {rows.map((row) => (
                    <li key={row.resource_uid} className="flex min-w-0 flex-col gap-2 rounded-md border p-2 text-sm">
                        <span className="break-all">
                            {row.external_resource_id} · {t(`project.settings.App resource state ${row.access_state}`)}
                        </span>
                        {row.is_selected && row.external_resource_id.startsWith("helm-release-") && (
                            <label className="flex min-w-0 flex-col gap-1">
                                {t("project.settings.devtron.helmTuple")}
                                <Input
                                    disabled={blocked || !!confirm}
                                    value={tuples[row.external_resource_id] ?? ""}
                                    onChange={(event) =>
                                        setTuples((old) => ({
                                            ...old,
                                            [row.external_resource_id]: event.target.value,
                                        }))
                                    }
                                    autoComplete="off"
                                />
                            </label>
                        )}
                        {row.is_selected && (
                            <div className="flex flex-wrap gap-2">
                                <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={blocked || !!confirm || !mayRead}
                                    onClick={() => void review("verify", row.external_resource_id, row)}
                                >
                                    {t("project.settings.devtron.verify")}
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={blocked || !!confirm}
                                    onClick={() => void review("remove", row.external_resource_id, row)}
                                >
                                    {t("project.settings.Remove app selection")}
                                </Button>
                            </div>
                        )}
                    </li>
                ))}
            </ul>
            {data?.next_cursor && (
                <Button
                    size="sm"
                    variant="outline"
                    disabled={blocked || !!confirm}
                    onClick={() => void load(lifetime.current.signal, data.next_cursor!)}
                >
                    {t("project.settings.devtron.moreResources")}
                </Button>
            )}
            {data && !rows.length && <p className="text-sm text-muted-foreground">{t("project.settings.No selected App resources")}</p>}
            {confirm && (
                <div className="flex min-w-0 flex-col gap-2 rounded-md border p-3" role="group">
                    <p className="break-all text-sm">{confirm.id}</p>
                    <p className="text-sm text-muted-foreground">
                        {t(`project.settings.devtron.${confirm.action === "verify" ? "verifyHelp" : "selectionHelp"}`)}
                    </p>
                    <div className="flex flex-wrap gap-2">
                        <Button size="sm" disabled={blocked} onClick={() => void save()}>
                            {t(
                                `project.settings.devtron.${confirm.action === "verify" ? "confirmVerify" : confirm.action === "remove" ? "confirmRemove" : "confirmSelection"}`
                            )}
                        </Button>
                        <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>
                            {t("common.Cancel")}
                        </Button>
                    </div>
                </div>
            )}
        </section>
    );
}
