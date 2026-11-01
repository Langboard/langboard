import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import type { AuthUser } from "@/core/models";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface InstallationRequest {
    uid: string;
    requester_uid: string;
    project_uid: string | null;
    source: "official" | "manual";
    app_key: string;
    name: string;
    version: string;
    sha256: string;
    size: number;
    provenance: unknown;
    expected_app_revision: string | null;
    decision: "pending" | "approved" | "rejected";
    installation_status: "not_started" | "installed" | "failed";
    installation_error: string | null;
    reason: string | null;
    revision: string;
}
interface RequestPage {
    items: InstallationRequest[];
    next_cursor: string | null;
}
const root = "/apps/installation-requests";

export default function AppInstallationRequests({ currentUser, projectUid }: { currentUser: AuthUser.TModel; projectUid?: string }) {
    return (
        <RequestJourney key={`${currentUser.uid}:${currentUser.is_admin}:${projectUid ?? ""}`} currentUser={currentUser} projectUid={projectUid} />
    );
}

function RequestJourney({ currentUser, projectUid }: { currentUser: AuthUser.TModel; projectUid?: string }) {
    const [t] = useTranslation();
    const [pending, setPending] = useState(false);
    const [uncertain, setUncertain] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [selected, setSelected] = useState<InstallationRequest | null>(null);
    const [reason, setReason] = useState("");
    const picker = useRef<HTMLInputElement>(null);
    const list = useInfiniteQuery({
        queryKey: ["app-installation-requests", currentUser.uid, currentUser.is_admin, projectUid],
        initialPageParam: "",
        queryFn: async ({ pageParam, signal }) =>
            (await api.get<RequestPage>(root, { signal, params: { after: pageParam || undefined, limit: 25 } })).data,
        getNextPageParam: (last) => last.next_cursor || undefined,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const catalog = useQuery({
        queryKey: ["installation-request-catalog", currentUser.uid, projectUid],
        enabled: !currentUser.is_admin,
        queryFn: async ({ signal }) =>
            (
                await api.get<{ official: { key: string; name: string; description: string; kind?: string }[] }>("/apps/catalog", {
                    signal,
                    params: { project_uid: projectUid },
                })
            ).data,
        retry: false,
    });
    const refresh = async () => {
        const readback = await list.refetch();
        if (selected) {
            try {
                setSelected((await api.get<{ request: InstallationRequest }>(`${root}/${selected.uid}`)).data.request);
            } catch {
                setUncertain(true);
                return;
            }
        }
        if (!readback.isError) setUncertain(false);
    };
    const submit = async (key?: string) => {
        if (pending || uncertain || list.isError || !list.data) return;
        setPending(true);
        try {
            // Persist the idempotency key before sending. A lost response and a reload reuse it.
            const digest =
                key ??
                (file
                    ? Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())), (b) =>
                          b.toString(16).padStart(2, "0")
                      ).join("")
                    : "");
            if (!digest) return;
            const storageKey = `app-install-request:${currentUser.uid}:${projectUid ?? ""}:${key ? "official" : "manual"}:${digest}`;
            const requestKey = sessionStorage.getItem(storageKey) ?? crypto.randomUUID();
            sessionStorage.setItem(storageKey, requestKey);
            let response;
            if (key)
                response = await api.post<{ request: InstallationRequest }>(`${root}/official/${encodeURIComponent(key)}`, {
                    request_key: requestKey,
                    ...(projectUid ? { project_uid: projectUid } : {}),
                });
            else {
                if (!file || !file.size || file.size > 8 * 1024 * 1024) return;
                const form = new FormData();
                form.append("file", file);
                form.append("request_key", requestKey);
                if (projectUid) form.append("project_uid", projectUid);
                response = await api.post<{ request: InstallationRequest }>(`${root}/upload`, form);
            }
            const receipt = response.data.request;
            if (!receipt?.uid || !receipt.revision) throw new Error("Missing request receipt");
            const readback = (await api.get<{ request: InstallationRequest }>(`${root}/${receipt.uid}`)).data.request;
            if (readback.uid !== receipt.uid || readback.revision !== receipt.revision) throw new Error("Request readback mismatch");
            setSelected(readback);
            const result = await list.refetch();
            if (result.isError) throw new Error("Request list readback failed");
            sessionStorage.removeItem(storageKey);
            setFile(null);
            if (picker.current) picker.current.value = "";
        } catch {
            setUncertain(true);
            await list.refetch();
        } finally {
            setPending(false);
        }
    };
    const review = async (decision: "approve" | "reject") => {
        if (!selected || pending || uncertain || list.isError) return;
        setPending(true);
        try {
            const receipt = (
                await api.post<{ request: InstallationRequest }>(`/settings/apps/installation-requests/${selected.uid}/review`, {
                    decision,
                    expected_revision: selected.revision,
                    reason,
                })
            ).data.request;
            const readback = (await api.get<{ request: InstallationRequest }>(`${root}/${selected.uid}`)).data.request;
            if (!receipt?.revision || readback.revision !== receipt.revision) throw new Error("Review readback mismatch");
            setSelected(readback);
            const result = await list.refetch();
            if (result.isError) throw new Error("Review list unavailable");
        } catch {
            setUncertain(true);
            await refresh();
        } finally {
            setPending(false);
        }
    };
    const disabled = pending || uncertain || list.isError || !list.data;
    return (
        <section className="mx-auto w-full max-w-6xl space-y-4 p-4 sm:p-6" aria-label={t("settings.App installation requests")}>
            <h2 className="text-xl font-semibold">{t("settings.App installation requests")}</h2>
            <p className="text-sm text-muted-foreground">{t("settings.Installation request consent help")}</p>
            {(uncertain || list.isError) && <p role="alert">{t("settings.Installation request uncertain")}</p>}
            <Button
                variant="outline"
                aria-label={t("settings.Refresh installation requests")}
                disabled={pending || list.isFetching}
                onClick={() => void refresh()}
            >
                {t("common.Refresh")}
            </Button>
            {!currentUser.is_admin && (
                <>
                    <h3 className="font-semibold">{t("settings.Official catalog")}</h3>
                    {catalog.isError && (
                        <Button variant="outline" onClick={() => void catalog.refetch()}>
                            {t("common.Retry")}
                        </Button>
                    )}
                    <div className="grid gap-4 md:grid-cols-2">
                        {catalog.data?.official
                            .filter((app) => app.kind !== "composition")
                            .map((app) => (
                                <article className="card card-border min-w-0 space-y-3 border p-4" key={app.key}>
                                    <h4 className="break-words font-semibold">{app.name}</h4>
                                    <p className="break-words text-sm">{app.description}</p>
                                    <Button variant="outline" disabled={disabled} onClick={() => void submit(app.key)}>
                                        {t("settings.Request installation")}: {app.name}
                                    </Button>
                                </article>
                            ))}
                    </div>
                    <label className="block text-sm" htmlFor="installation-request-file">
                        {t("settings.App ZIP package")}
                    </label>
                    <input
                        ref={picker}
                        id="installation-request-file"
                        className="w-full min-w-0 text-sm"
                        type="file"
                        accept=".zip,application/zip"
                        disabled={disabled}
                        aria-describedby="installation-request-size"
                        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                    />
                    <p id="installation-request-size" className="text-xs text-muted-foreground">
                        {t("settings.App ZIP size help")}
                    </p>
                    <Button variant="outline" disabled={disabled || !file || !file.size || file.size > 8 * 1024 * 1024} onClick={() => void submit()}>
                        {t("settings.Request ZIP installation")}
                    </Button>
                </>
            )}
            <div className="space-y-3">
                {list.data?.pages
                    .flatMap((page) => page.items)
                    .map((item) => (
                        <article className="min-w-0 space-y-2 rounded-xl border p-4" key={item.uid}>
                            <h3 className="break-words font-semibold">
                                {item.name} · {item.version}
                            </h3>
                            <p>
                                {t(`settings.Request decision ${item.decision}`)} · {t(`settings.Request installation ${item.installation_status}`)}
                            </p>
                            {item.installation_error && (
                                <p className="break-all" role="status">
                                    {item.installation_error}
                                </p>
                            )}
                            {item.reason && <p className="break-words">{item.reason}</p>}
                            <Button
                                variant="outline"
                                disabled={pending}
                                onClick={async () => {
                                    try {
                                        setSelected((await api.get<{ request: InstallationRequest }>(`${root}/${item.uid}`)).data.request);
                                        setReason("");
                                    } catch {
                                        setUncertain(true);
                                    }
                                }}
                            >
                                {t("settings.Review installation request")}: {item.name}
                            </Button>
                        </article>
                    ))}
            </div>
            {list.hasNextPage && (
                <Button disabled={list.isFetching} onClick={() => void list.fetchNextPage()}>
                    {t("settings.More installation requests")}
                </Button>
            )}
            {selected && (
                <article className="min-w-0 space-y-3 rounded-xl border p-4" aria-label={t("settings.Staged package identity")}>
                    <h3 className="break-words font-semibold">
                        {selected.name} · {selected.version}
                    </h3>
                    <p>
                        {t(`settings.Request decision ${selected.decision}`)} · {t(`settings.Request installation ${selected.installation_status}`)}
                    </p>
                    <dl className="space-y-2 break-all text-sm">
                        {Object.entries({
                            uid: selected.uid,
                            requester_uid: selected.requester_uid,
                            project_uid: selected.project_uid,
                            source: selected.source,
                            app_key: selected.app_key,
                            version: selected.version,
                            sha256: selected.sha256,
                            size: selected.size,
                            expected_app_revision: selected.expected_app_revision,
                            revision: selected.revision,
                        }).map(([label, value]) => (
                            <div key={label}>
                                <dt className="font-medium">{label}</dt>
                                <dd>{value ?? "—"}</dd>
                            </div>
                        ))}
                    </dl>
                    <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(selected.provenance, null, 2)}</pre>
                    {selected.installation_error && <p role="status">{selected.installation_error}</p>}
                    {selected.reason && <p className="break-words">{selected.reason}</p>}
                    {currentUser.is_admin && selected.decision === "pending" && (
                        <>
                            <label htmlFor="installation-review-reason">{t("settings.Installation review reason")}</label>
                            <Input
                                id="installation-review-reason"
                                value={reason}
                                maxLength={1000}
                                disabled={disabled}
                                onChange={(event) => setReason(event.target.value)}
                            />
                            <div className="flex flex-wrap gap-2">
                                <Button disabled={disabled} onClick={() => void review("approve")}>
                                    {t("settings.Approve installation request")}
                                </Button>
                                <Button variant="outline" disabled={disabled} onClick={() => void review("reject")}>
                                    {t("settings.Reject installation request")}
                                </Button>
                            </div>
                        </>
                    )}
                </article>
            )}
        </section>
    );
}
