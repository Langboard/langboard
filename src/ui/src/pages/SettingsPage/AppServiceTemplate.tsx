import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { eligibleWebhookOrigin } from "./appServiceWebhookCandidates";

interface Template {
    template_uid: string;
    revision: number;
    enabled: boolean;
    origin?: string;
}
interface Metadata {
    template: Template | null;
    current?: boolean;
}
interface Webhook {
    uid: string;
    name: string;
    url: string;
    events?: string[];
}

export default function AppServiceTemplate({ appKey, userUid, trust }: { appKey: string; userUid: string; trust?: { data_origins?: string[] } }) {
    const [t] = useTranslation();
    const path = `/settings/apps/registry/${encodeURIComponent(appKey)}/service-template`;
    const [webhookUid, setWebhookUid] = useState("");
    const [confirm, setConfirm] = useState<"bind" | "disable" | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef(false);
    const metadata = useQuery({
        queryKey: ["app-service-template", userUid, appKey],
        queryFn: async ({ signal }) => (await api.get<Metadata>(path, { signal })).data,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const webhooks = useQuery({
        queryKey: ["app-service-template-webhooks", userUid, appKey],
        queryFn: async ({ signal }) => (await api.get<{ webhooks: Webhook[] }>("/settings/webhooks", { signal })).data.webhooks,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const candidates = (webhooks.data ?? []).filter((item) => eligibleWebhookOrigin(item, trust?.data_origins));
    const selected = candidates.find((item) => item.uid === webhookUid);
    const origin = (url: string) => new URL(url).origin;
    const unavailable = pending || metadata.isFetching || metadata.isError || !metadata.data || failed;
    const submit = async () => {
        if (writing.current || unavailable || !confirm) return;
        if (confirm === "bind" && !selected) return;
        if (confirm === "disable" && !metadata.data?.template?.enabled) return;
        writing.current = true;
        setPending(true);
        try {
            const receipt = (
                await api.post<Template>(confirm === "disable" ? `${path}/disable` : path, {
                    ...(confirm === "bind" ? { webhook_uid: webhookUid } : {}),
                    expected_revision: metadata.data?.template?.revision ?? null,
                })
            ).data;
            const readback = await metadata.refetch();
            const saved = readback.data?.template;
            if (
                readback.isError ||
                !saved ||
                saved.template_uid !== receipt.template_uid ||
                saved.revision !== receipt.revision ||
                saved.enabled !== receipt.enabled
            )
                throw new Error("Service template readback mismatch");
            setConfirm(null);
        } catch {
            setFailed(true);
            setConfirm(null);
        } finally {
            writing.current = false;
            setPending(false);
        }
    };
    const refresh = async () => {
        if (writing.current) return;
        setConfirm(null);
        const results = await Promise.all([metadata.refetch(), webhooks.refetch()]);
        if (results.every((result) => !result.isError)) setFailed(false);
    };
    return (
        <section className="space-y-3 rounded-2xl border bg-card p-5" aria-busy={pending}>
            <h3 className="font-semibold">{t("settings.App service template")}</h3>
            <p className="text-sm text-muted-foreground">{t("settings.App service template help")}</p>
            {(failed || metadata.isError || webhooks.isError) && <p role="alert">{t("settings.App service change failed")}</p>}
            <Button size="sm" variant="outline" disabled={pending || metadata.isFetching || webhooks.isFetching} onClick={() => void refresh()}>
                {t("settings.Refresh service settings")}
            </Button>
            {metadata.data && (
                <p className="break-all text-sm">
                    {metadata.data.template ? (
                        <>
                            {metadata.data.template.origin} ·{" "}
                            {t(metadata.data.current ? "settings.Service template ready" : "settings.Service template unavailable")}
                        </>
                    ) : (
                        t("settings.No service template")
                    )}
                </p>
            )}
            <label className="block space-y-2 text-sm">
                <span>{t("settings.App service webhook")}</span>
                <select
                    className="h-10 w-full rounded-md border bg-background px-3"
                    value={webhookUid}
                    disabled={unavailable || webhooks.isFetching || webhooks.isError || !!confirm}
                    onChange={(event) => setWebhookUid(event.target.value)}
                >
                    <option value="">{t("settings.Select service webhook")}</option>
                    {candidates.map((item) => (
                        <option key={item.uid} value={item.uid}>
                            {item.name} · {origin(item.url)}
                        </option>
                    ))}
                </select>
            </label>
            {webhooks.data && candidates.length === 0 && (
                <p className="text-sm text-muted-foreground">{t("settings.No eligible service webhooks")}</p>
            )}
            <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={unavailable || !selected || !!confirm} onClick={() => setConfirm("bind")}>
                    {t("settings.Bind service template")}
                </Button>
                <Button
                    size="sm"
                    variant="outline"
                    disabled={unavailable || !metadata.data?.template?.enabled || !!confirm}
                    onClick={() => setConfirm("disable")}
                >
                    {t("settings.Disable service template")}
                </Button>
            </div>
            {confirm && (
                <div className="space-y-3 rounded-md border p-3">
                    <p className="text-sm">
                        {t(confirm === "bind" ? "settings.Confirm service binding help" : "settings.Confirm service disable help")}
                    </p>
                    {confirm === "bind" && selected && <p className="break-all text-sm">{origin(selected.url)}</p>}
                    <div className="flex gap-2">
                        <Button size="sm" disabled={unavailable} onClick={() => void submit()}>
                            {t("common.Confirm")}
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
