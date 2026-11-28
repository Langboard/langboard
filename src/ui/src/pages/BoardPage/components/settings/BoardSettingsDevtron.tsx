import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { api } from "@/core/helpers/Api";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { confirmResourceWrite, ResourceSelectionConflict } from "@/core/apps/ResourceSelection";
import {
    devtronTarget,
    readDevtronAccounts,
    readDevtronConnection,
    secureDevtronInput,
    type DevtronAccounts,
    type DevtronConnection,
    type DevtronProviderError,
    type DevtronScope,
} from "@/core/apps/DevtronSettings";
import DevtronScopeFields from "./DevtronScopeFields";
import DevtronResources from "./DevtronResources";

interface Props {
    app: CatalogApp;
    onStatusChange?: () => void;
    onBusy?: (busy: boolean) => void;
}
export default function BoardSettingsDevtron(props: Props) {
    const { project, currentUser, canEditBasicInfo } = useBoardSettings();
    return (
        <DevtronSettings
            key={`${currentUser.uid}:${project.uid}:${canEditBasicInfo}`}
            {...props}
            projectUID={project.uid}
            userUID={currentUser.uid}
            canEditBasicInfo={canEditBasicInfo}
        />
    );
}

export function DevtronSettings({
    app,
    projectUID,
    userUID,
    canEditBasicInfo,
    onStatusChange,
    onBusy,
}: Props & {
    projectUID: string;
    userUID: string;
    canEditBasicInfo: boolean;
}) {
    const [t] = useTranslation();
    const root = `/board/${projectUID}/settings/apps/devtron-panel`;
    const [organization, setOrganization] = useState<string>();
    const [after, setAfter] = useState<string>();
    const context = JSON.stringify([userUID, projectUID, organization]);
    const liveContext = useRef(context);
    liveContext.current = context;
    const lifetime = useRef(new AbortController());
    const guard = useRef({ busy: false, needsRefresh: false });
    const [connection, setConnection] = useState<DevtronConnection | null>(null);
    const [instance, setInstance] = useState("");
    const [reference, setReference] = useState("");
    const [target, setTarget] = useState<DevtronScope>({
        kind: "inventory",
        first: "",
        second: "",
    });
    const [input, setInput] = useState<{
        input_uid: string;
        input_url: string;
    } | null>(null);
    const [confirm, setConfirm] = useState<"connect" | "disconnect" | null>(null);
    const [reviewed, setReviewed] = useState<{
        instance_url: string;
        credential_reference: string;
        deployment_scope?: Record<string, string | number>;
    } | null>(null);
    const [pending, setPending] = useState(false);
    const [resourceBusy, setResourceBusy] = useState(false);
    const [failed, setFailed] = useState(false);
    const [notice, setNotice] = useState("");
    const [resourceEpoch, setResourceEpoch] = useState(0);
    const config = { env: { interceptToast: true } as never };
    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        guard.current = { busy: false, needsRefresh: false };
        setConnection(null);
        setInstance("");
        setReference("");
        setInput(null);
        setConfirm(null);
        setReviewed(null);
        setPending(false);
        setResourceBusy(false);
        setFailed(false);
        setNotice("");
        return () => controller.abort();
    }, [context]);
    useEffect(() => {
        onBusy?.(pending || resourceBusy);
        return () => onBusy?.(false);
    }, [pending, resourceBusy, onBusy]);
    const readAccounts = async (signal: AbortSignal, exact?: string) =>
        readDevtronAccounts(
            (
                await api.get<DevtronAccounts>(`${root}/connections`, {
                    ...config,
                    signal,
                    params: {
                        organization_uid: organization,
                        ...(exact ? { connection_uid: exact } : { after }),
                    },
                })
            ).data
        );
    const list = useQuery({
        queryKey: ["devtron-accounts", context, after],
        queryFn: ({ signal }) => readAccounts(AbortSignal.any([signal, lifetime.current.signal])),
        enabled: canEditBasicInfo,
        retry: 0,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const refresh = async () => {
        const submitted = context;
        const result = await list.refetch();
        if (liveContext.current !== submitted) return;
        setFailed(result.isError);
        if (!result.isError) {
            guard.current.needsRefresh = false;
            setConfirm(null);
            setResourceEpoch((value) => value + 1);
            setConnection((old) => result.data?.items.find((row) => row.connection_uid === old?.connection_uid) ?? old);
            onStatusChange?.();
        }
    };
    const reviewConnection = async () => {
        const submitted = context,
            signal = lifetime.current.signal;
        if (pending || resourceBusy || guard.current.busy) return;
        setPending(true);
        setNotice("");
        try {
            const selectedTarget = await devtronTarget(target);
            if (liveContext.current !== submitted || signal.aborted) return;
            setReviewed({
                instance_url: instance.trim(),
                credential_reference: reference.trim(),
                deployment_scope: selectedTarget.deployment_scope,
            });
            setConfirm("connect");
        } catch {
            if (liveContext.current === submitted && !signal.aborted) setNotice(t("project.settings.devtron.invalidTarget"));
        } finally {
            if (liveContext.current === submitted && !signal.aborted) setPending(false);
        }
    };
    const write = async (operation: "connect" | "disconnect" | "input") => {
        const shown = list.data,
            selected = connection,
            submitted = context,
            signal = lifetime.current.signal;
        if (!shown || !canEditBasicInfo || pending || resourceBusy || failed || list.isError || guard.current.busy || guard.current.needsRefresh)
            return;
        if (operation !== "disconnect" && (!shown.available || shown.app_revision !== app.app_revision)) return;
        if (
            (operation === "connect" && (confirm !== "connect" || !reviewed)) ||
            (operation === "disconnect" && (!selected || confirm !== "disconnect"))
        )
            return;
        setPending(true);
        setNotice("");
        const valid = () => !signal.aborted && liveContext.current === submitted;
        let account: DevtronConnection | null = null;
        try {
            await confirmResourceWrite(
                guard.current,
                signal,
                async () => {
                    const fresh = await readAccounts(signal, operation === "disconnect" ? selected!.connection_uid : undefined);
                    if (operation === "disconnect") {
                        if (fresh.items[0]?.connection_uid !== selected!.connection_uid || fresh.items[0]?.revision !== selected!.revision)
                            throw new ResourceSelectionConflict("Connection changed");
                    } else if (fresh.app_revision !== shown.app_revision || !fresh.available)
                        throw new ResourceSelectionConflict("App authority changed");
                },
                async () => {
                    if (operation === "input") {
                        const result = (
                            await api.post<{ input_uid: string; input_url: string }>(
                                `${root}/secret-input`,
                                { app_revision: shown.app_revision },
                                { ...config, signal }
                            )
                        ).data;
                        const input_url = secureDevtronInput(result, location.origin);
                        if (valid()) setInput({ input_uid: result.input_uid, input_url });
                    } else {
                        const result = (
                            await api.post<DevtronConnection | DevtronProviderError>(
                                operation === "connect" ? `${root}/connections` : `${root}/connections/${selected!.connection_uid}/disconnect`,
                                operation === "connect"
                                    ? {
                                          ...reviewed,
                                          app_revision: shown.app_revision,
                                          organization_uid: organization ?? null,
                                      }
                                    : { expected_revision: selected!.revision },
                                { ...config, signal }
                            )
                        ).data;
                        if ("ok" in result && result.ok === false) {
                            if (valid())
                                setNotice(
                                    t("project.settings.devtron.providerError", {
                                        status: result.error.status ?? "—",
                                    })
                                );
                        } else account = readDevtronConnection(result as DevtronConnection);
                    }
                },
                valid
            );
            if (!valid()) return;
            if (account) {
                setConnection(account);
                setReference("");
                setInput(null);
            }
            setConfirm(null);
            const result = await list.refetch();
            if (!valid()) return;
            setFailed(result.isError);
            if (result.isError) guard.current.needsRefresh = true;
            setResourceEpoch((value) => value + 1);
            onStatusChange?.();
        } catch {
            if (valid()) {
                setFailed(true);
                setConfirm(null);
                setInput(null);
            }
        } finally {
            if (valid()) setPending(false);
        }
    };
    const checkInput = async () => {
        const submitted = context,
            signal = lifetime.current.signal;
        if (!input || pending || resourceBusy || guard.current.busy || !list.data) return;
        setPending(true);
        try {
            const result = (
                await api.get<{ state: string; secret_ref?: string }>(`${root}/secret-input/${input.input_uid}`, {
                    ...config,
                    signal,
                    params: { app_revision: list.data.app_revision },
                })
            ).data;
            if (signal.aborted || liveContext.current !== submitted) return;
            if (result.state === "completed" && /^secret:\/\/ref\/[A-Za-z0-9]{1,11}$/.test(result.secret_ref ?? "")) {
                setReference(result.secret_ref!);
                setInput(null);
            } else if (result.state !== "pending") setFailed(true);
        } catch {
            if (!signal.aborted && liveContext.current === submitted) setFailed(true);
        } finally {
            if (!signal.aborted && liveContext.current === submitted) setPending(false);
        }
    };
    if (!canEditBasicInfo) return <p>{t("project.settings.devtron.updateRequired")}</p>;
    const data = failed || list.isError ? undefined : list.data,
        busy = pending || resourceBusy || list.isFetching;
    const blocked = busy || failed || list.isError;
    return (
        <section className="flex min-w-0 flex-col gap-3" aria-busy={busy}>
            <h4 className="font-semibold">{t("project.settings.devtron.title")}</h4>
            <p className="text-sm text-muted-foreground">{t("project.settings.devtron.accountHelp")}</p>
            {(failed || list.isError) && <p role="alert">{t("project.settings.devtron.refreshRequired")}</p>}
            {notice && <p role="status">{notice}</p>}
            <Button size="sm" variant="outline" className="self-start" disabled={busy} onClick={() => void refresh()}>
                {t("project.settings.devtron.refresh")}
            </Button>
            <fieldset className="fieldset flex min-w-0 flex-col gap-3" disabled={blocked || !!confirm}>
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t("project.settings.devtron.ownership")}
                    <select
                        className="select min-h-10 w-full min-w-0 rounded-md border bg-background px-3"
                        value={organization ?? ""}
                        onChange={(event) => {
                            setOrganization(event.target.value || undefined);
                            setAfter(undefined);
                        }}
                    >
                        {(data?.ownership_scopes ?? [null]).map((uid) => (
                            <option key={uid ?? "personal"} value={uid ?? ""}>
                                {t(`project.settings.devtron.${uid === null ? "personal" : "organization"}`)}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {t("project.settings.devtron.connection")}
                    <select
                        className="select min-h-10 w-full min-w-0 rounded-md border bg-background px-3"
                        value={connection?.connection_uid ?? ""}
                        onChange={(event) => {
                            setConnection(data?.items.find((row) => row.connection_uid === event.target.value) ?? null);
                            setNotice("");
                        }}
                    >
                        <option value="">{t("project.settings.devtron.newConnection")}</option>
                        {data && connection && !data.items.some((row) => row.connection_uid === connection.connection_uid) && (
                            <option value={connection.connection_uid}>{connection.instance_url}</option>
                        )}
                        {data?.items.map((row) => (
                            <option key={row.connection_uid} value={row.connection_uid}>
                                {row.instance_url} · {t(`project.settings.App resource state ${row.state}`)} · {row.connection_uid.slice(-4)}
                            </option>
                        ))}
                    </select>
                </label>
                {data?.next_cursor && (
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                            setConnection(null);
                            setAfter(data.next_cursor!);
                        }}
                    >
                        {t("project.settings.devtron.moreConnections")}
                    </Button>
                )}
                {after && (
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                            setConnection(null);
                            setAfter(undefined);
                        }}
                    >
                        {t("project.settings.devtron.firstConnections")}
                    </Button>
                )}
                {!connection && (
                    <>
                        <label className="flex min-w-0 flex-col gap-1 text-sm">
                            {t("project.settings.devtron.instance")}
                            <Input type="url" value={instance} onChange={(event) => setInstance(event.target.value)} autoComplete="off" />
                        </label>
                        <p className="text-xs text-muted-foreground">{t("project.settings.devtron.instanceHelp")}</p>
                        <label className="flex min-w-0 flex-col gap-1 text-sm">
                            {t("project.settings.devtron.reference")}
                            <Input value={reference} onChange={(event) => setReference(event.target.value)} autoComplete="off" />
                        </label>
                        {!organization && (
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={!data?.available || data.app_revision !== app.app_revision}
                                onClick={() => void write("input")}
                            >
                                {t("project.settings.devtron.storeCredential")}
                            </Button>
                        )}
                        {input && (
                            <div className="flex flex-wrap items-center gap-2 text-sm">
                                <a className="underline" href={input.input_url} target="_blank" rel="noopener noreferrer">
                                    {t("project.settings.Open secure token input")}
                                </a>
                                <Button size="sm" variant="outline" onClick={() => void checkInput()}>
                                    {t("project.settings.Check token input")}
                                </Button>
                            </div>
                        )}
                        <DevtronScopeFields value={target} onChange={setTarget} inventory />
                        <Button
                            size="sm"
                            disabled={
                                !data?.available ||
                                data.app_revision !== app.app_revision ||
                                !instance.trim() ||
                                !/^secret:\/\/ref\/[A-Za-z0-9]{1,11}$/.test(reference.trim())
                            }
                            onClick={() => void reviewConnection()}
                        >
                            {t("project.settings.devtron.reviewConnection")}
                        </Button>
                    </>
                )}
                {connection && connection.state !== "disconnected" && (
                    <Button size="sm" variant="outline" onClick={() => setConfirm("disconnect")}>
                        {t("project.settings.devtron.disconnect")}
                    </Button>
                )}
            </fieldset>
            {data && confirm && (
                <div className="flex min-w-0 flex-col gap-2 rounded-md border p-3" role="group">
                    <p className="break-all text-sm">{confirm === "connect" ? instance.trim() : connection?.instance_url}</p>
                    <p className="text-sm text-muted-foreground">
                        {t(`project.settings.devtron.${confirm === "connect" ? "connectHelp" : "disconnectHelp"}`)}
                    </p>
                    <div className="flex flex-wrap gap-2">
                        <Button size="sm" disabled={blocked} onClick={() => void write(confirm)}>
                            {t(`project.settings.devtron.${confirm === "connect" ? "confirmConnection" : "confirmDisconnect"}`)}
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirm(null)}>
                            {t("common.Cancel")}
                        </Button>
                    </div>
                </div>
            )}
            {connection && data && (
                <DevtronResources
                    key={`${context}:${connection.connection_uid}:${resourceEpoch}`}
                    app={app}
                    projectUID={projectUID}
                    connection={connection}
                    metadata={data}
                    guard={guard.current}
                    disabled={pending || failed || list.isError || !!confirm}
                    onBusy={setResourceBusy}
                    onRefresh={() => void refresh()}
                    onRequireRefresh={() => setFailed(true)}
                    onStatusChange={() => {
                        void list.refetch();
                        onStatusChange?.();
                    }}
                />
            )}
        </section>
    );
}
