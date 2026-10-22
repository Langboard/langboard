import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";
import Button from "@/components/base/Button";
import Input, { InputVariants } from "@/components/base/Input";

type Connection = {
    uid: string;
    endpoint: string;
    index: string;
    secret_ref: string;
    secret_revision: number;
    active: bool;
    pending_cleanup: number;
    reference_available?: bool;
    blocked_index_jobs?: number;
};
type Settings = {
    revision: number;
    mode: string;
    reads_enabled: bool;
    indexing_enabled: bool;
    selected_connection_uid: string | null;
    selected_connection?: Connection | null;
    connections: Connection[];
    legacy_pending_cleanup: number;
    next_cursor?: string | null;
    secret_references_truncated?: bool;
    secret_references?: { uri: string; name: string; state: string }[];
    blocked_index_jobs?: number;
};
const url = "/settings/keyword-search";

export default function KeywordSearchPage() {
    const [t] = useTranslation();
    const [settings, setSettings] = useState<Settings>();
    const [endpoint, setEndpoint] = useState("");
    const [index, setIndex] = useState("");
    const [secretRef, setSecretRef] = useState("");
    const [reads, setReads] = useState(false);
    const [indexing, setIndexing] = useState(false);
    const [name, setName] = useState("keyword/elasticsearch");
    const [input, setInput] = useState<{ input_uid: string; input_url: string }>();
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState(false);
    const [preflight, setPreflight] = useState<{ code: string; ok: bool; version_verified?: bool }>();
    const accept = (data: Settings) => {
        setSettings(data);
        const connection = data.selected_connection ?? data.connections.find((item) => item.uid === data.selected_connection_uid);
        setEndpoint(connection?.endpoint ?? "");
        setIndex(connection?.index ?? "");
        setSecretRef(connection?.secret_ref ?? "");
        setReads(data.reads_enabled);
        setIndexing(data.indexing_enabled);
    };
    const run = async (action: () => Promise<void>) => {
        setBusy(true);
        setFailed(false);
        try {
            await action();
        } catch {
            setFailed(true);
        } finally {
            setBusy(false);
        }
    };
    useEffect(() => {
        void run(async () => accept((await api.get(url)).data));
    }, []);
    return (
        <section className="mx-auto w-full max-w-3xl rounded-xl border border-border bg-background shadow-sm">
            <div className="flex min-w-0 flex-col gap-5 p-4 sm:p-6">
                <h1 className="text-xl font-semibold tracking-tight">{t("settings.keywordSearch.title")}</h1>
                <p className="text-sm leading-6 text-muted-foreground">{t("settings.keywordSearch.effects")}</p>
                <p className="rounded-lg border border-border bg-muted/30 p-3 text-sm leading-6">{t("settings.keywordSearch.retention")}</p>
                {failed && <p role="alert">{t("settings.keywordSearch.failure")}</p>}
                <Button variant="outline" disabled={busy} onClick={() => void run(async () => accept((await api.get(url)).data))}>
                    {t("settings.keywordSearch.reload")}
                </Button>
                {settings && (
                    <fieldset disabled={busy} className="flex min-w-0 flex-col gap-4">
                        <p>
                            {t("settings.keywordSearch.mode")}:{" "}
                            {t(`settings.keywordSearch.${settings.mode === "native" ? "nativeMode" : "legacyMode"}`)} ·{" "}
                            {t("settings.keywordSearch.revision")}: {settings.revision}
                        </p>
                        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                            {t("settings.keywordSearch.connection")}
                            <select
                                className={InputVariants()}
                                value={settings.selected_connection_uid ?? ""}
                                onChange={(event) => {
                                    const connection =
                                        settings.connections.find((item) => item.uid === event.target.value) ??
                                        (settings.selected_connection?.uid === event.target.value ? settings.selected_connection : undefined);
                                    setSettings({ ...settings, selected_connection_uid: connection?.uid ?? null });
                                    setEndpoint(connection?.endpoint ?? "");
                                    setIndex(connection?.index ?? "");
                                    setSecretRef(connection?.secret_ref ?? "");
                                    setPreflight(undefined);
                                }}
                            >
                                <option value="">{t("settings.keywordSearch.newConnection")}</option>
                                {settings.selected_connection &&
                                    !settings.connections.some((item) => item.uid === settings.selected_connection?.uid) && (
                                        <option value={settings.selected_connection.uid}>
                                            {settings.selected_connection.endpoint} / {settings.selected_connection.index}
                                        </option>
                                    )}
                                {settings.connections
                                    .filter((item) => item.active)
                                    .map((item) => (
                                        <option key={item.uid} value={item.uid}>
                                            {item.endpoint} / {item.index}
                                        </option>
                                    ))}
                            </select>
                        </label>
                        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                            {t("settings.keywordSearch.endpoint")}
                            <Input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} />
                        </label>
                        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                            {t("settings.keywordSearch.index")}
                            <Input value={index} onChange={(event) => setIndex(event.target.value)} />
                        </label>
                        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                            {t("settings.keywordSearch.secretRef")}
                            <Input
                                value={secretRef}
                                placeholder="secret://ref/…"
                                list="keyword-instance-secrets"
                                onChange={(event) => setSecretRef(event.target.value)}
                            />
                        </label>
                        <datalist id="keyword-instance-secrets">
                            {settings.secret_references
                                ?.filter((item) => item.state === "active")
                                .map((item) => (
                                    <option key={item.uri} value={item.uri}>
                                        {item.name}
                                    </option>
                                ))}
                        </datalist>
                        <label className="flex items-center gap-2">
                            <input
                                className="h-4 w-4 rounded border border-input"
                                type="checkbox"
                                checked={reads}
                                onChange={(event) => setReads(event.target.checked)}
                            />
                            {t("settings.keywordSearch.reads")}
                        </label>
                        <label className="flex items-center gap-2">
                            <input
                                className="h-4 w-4 rounded border border-input"
                                type="checkbox"
                                checked={indexing}
                                onChange={(event) => setIndexing(event.target.checked)}
                            />
                            {t("settings.keywordSearch.indexing")}
                        </label>
                        <Button
                            disabled={busy}
                            onClick={() =>
                                void run(async () => {
                                    const reference = secretRef
                                        ? (await api.get(`/secret-references/${secretRef.split("/").at(-1)}`)).data.reference
                                        : null;
                                    accept(
                                        (
                                            await api.put(url, {
                                                expected_revision: settings.revision,
                                                endpoint,
                                                index,
                                                secret_ref: secretRef,
                                                secret_revision: reference?.revision ?? 0,
                                                reads_enabled: reads,
                                                indexing_enabled: indexing,
                                            })
                                        ).data
                                    );
                                    setPreflight(undefined);
                                })
                            }
                        >
                            {t("settings.keywordSearch.save")}
                        </Button>
                        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                            {t("settings.keywordSearch.secretName")}
                            <Input value={name} onChange={(event) => setName(event.target.value)} />
                        </label>
                        <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => void run(async () => setInput((await api.post(`${url}/secret-input`, { name })).data))}
                        >
                            {t("settings.keywordSearch.createSecret")}
                        </Button>
                        {input && (
                            <div className="flex flex-col gap-2 sm:flex-row">
                                <a className="link" href={input.input_url} target="_blank" rel="noreferrer">
                                    {t("settings.keywordSearch.openInput")}
                                </a>
                                <Button
                                    variant="outline"
                                    disabled={busy}
                                    onClick={() =>
                                        void run(async () => {
                                            const status = (await api.get(`${url}/secret-input/${input.input_uid}`)).data;
                                            if (status.state !== "completed") throw new Error("Input incomplete");
                                            setSecretRef(status.secret_ref);
                                            setInput(undefined);
                                        })
                                    }
                                >
                                    {t("settings.keywordSearch.checkInput")}
                                </Button>
                            </div>
                        )}
                        <p className="text-sm leading-6 text-muted-foreground">{t("settings.keywordSearch.preflightLimits")}</p>
                        {settings.selected_connection_uid && (
                            <Button
                                variant="outline"
                                disabled={busy}
                                onClick={() =>
                                    void run(async () => {
                                        const result = (await api.post(`${url}/connections/${settings.selected_connection_uid}/preflight`)).data;
                                        setPreflight(result);
                                    })
                                }
                            >
                                {t("settings.keywordSearch.preflight")}
                            </Button>
                        )}
                        {preflight && (
                            <div role="status" className="break-words">
                                <p>{t(`settings.keywordSearch.${preflight.ok ? "preflightPassed" : "preflightFailed"}`)}</p>
                                <p>{t(`settings.keywordSearch.${preflight.version_verified ? "versionVerified" : "versionUnverified"}`)}</p>
                                <details>
                                    <summary>{t("settings.keywordSearch.diagnostics")}</summary>
                                    <code>{preflight.code}</code>
                                </details>
                            </div>
                        )}
                        <ul className="flex flex-col gap-3">
                            {settings.connections.map((item) => (
                                <li key={item.uid} className="min-w-0 break-words rounded border p-3">
                                    <p>
                                        {item.endpoint} / {item.index} ·{" "}
                                        {!item.active
                                            ? t("settings.keywordSearch.revoked")
                                            : t(`settings.keywordSearch.${item.reference_available === false ? "referenceUnavailable" : "active"}`)}
                                    </p>
                                    <p>
                                        <span>
                                            {t("settings.keywordSearch.cleanup")}: {item.pending_cleanup}
                                        </span>
                                        {" · "}
                                        <span>{t("settings.keywordSearch.blockedIndex")}: </span>
                                        {item.blocked_index_jobs ?? 0}
                                    </p>
                                    {item.active && (
                                        <Button
                                            variant="outline"
                                            disabled={busy}
                                            onClick={() =>
                                                void run(async () =>
                                                    accept(
                                                        (
                                                            await api.post(`${url}/connections/${item.uid}/revoke`, {
                                                                expected_revision: settings.revision,
                                                            })
                                                        ).data
                                                    )
                                                )
                                            }
                                        >
                                            {t("settings.keywordSearch.revoke")}
                                        </Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                        {settings.next_cursor && (
                            <Button
                                variant="outline"
                                disabled={busy}
                                onClick={() =>
                                    void run(async () => {
                                        const data: Settings = (await api.get(`${url}?cursor=${settings.next_cursor}`)).data;
                                        if (data.revision !== settings.revision) throw new Error("Settings changed");
                                        setSettings({
                                            ...settings,
                                            next_cursor: data.next_cursor,
                                            connections: [...settings.connections, ...data.connections],
                                        });
                                    })
                                }
                            >
                                {t("settings.keywordSearch.olderConnections")}
                            </Button>
                        )}
                        {settings.secret_references_truncated && <p>{t("settings.keywordSearch.referencesLimit")}</p>}
                        <p>
                            {t("settings.keywordSearch.legacyCleanup")}: {settings.legacy_pending_cleanup}
                        </p>
                    </fieldset>
                )}
            </div>
        </section>
    );
}
