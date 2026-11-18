import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { canHandoffCustody, type CustodyMetadata, type CustodyReceipt, type HandoffReceipt } from "./appCredentialCustodyState";

interface ServiceTemplate {
    template: { template_uid: string; revision: number; origin: string };
    destination_revision: number | null;
    can_prepare: boolean;
}
type Action = "prepare" | "approve" | "handoff" | "revoke";
interface Confirmation {
    action: Action;
    path: string;
    body: Record<string, unknown>;
    origin: string;
}

export default function BoardSettingsCredentialCustody({
    connectionUID,
    userUID,
    canConfigure,
    onBusy,
    showHandoff = true,
}: {
    connectionUID: string;
    userUID: string;
    canConfigure: boolean;
    onBusy: (busy: boolean) => void;
    showHandoff?: boolean;
}) {
    const [t] = useTranslation();
    const path = `/settings/apps/connections/${connectionUID}`;
    const [confirm, setConfirm] = useState<Confirmation | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef(false);
    const config = { env: { interceptToast: true } as never };
    const metadata = useQuery({
        queryKey: ["app-credential-custody", userUID, connectionUID],
        queryFn: async ({ signal }) => (await api.get<CustodyMetadata>(`${path}/credential-custody`, { ...config, signal })).data,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const service = useQuery({
        queryKey: ["connection-service-template", userUID, connectionUID],
        queryFn: async ({ signal }) => (await api.get<ServiceTemplate>(`${path}/service-template`, { ...config, signal })).data,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    useEffect(() => {
        onBusy(pending);
        return () => onBusy(false);
    }, [pending, onBusy]);
    const busy = pending || metadata.isFetching || service.isFetching;
    const blocked = busy || failed || metadata.isError || !metadata.data;
    const data = metadata.data;
    const custody = data?.custody;
    const refresh = async () => {
        if (writing.current) return;
        setConfirm(null);
        const result = await metadata.refetch();
        await service.refetch();
        setFailed(result.isError);
    };
    const choose = (action: Action) => {
        if (blocked || confirm || !data) return;
        const origin = data.destination?.origin ?? "";
        if (action === "prepare" && canConfigure && service.data?.can_prepare && !service.isError) {
            setConfirm({
                action,
                path: `${path}/service-destination`,
                origin: service.data.template.origin,
                body: {
                    expected_template_revision: service.data.template.revision,
                    expected_destination_revision: service.data.destination_revision,
                },
            });
        } else if (action === "approve" && canConfigure && data.destination) {
            setConfirm({
                action,
                path: `${path}/credential-custody`,
                origin,
                body: {
                    expected_connection_revision: data.connection_revision,
                    expected_destination_revision: data.destination.revision,
                    expected_revision: custody?.revision ?? null,
                },
            });
        } else if (action === "handoff" && canConfigure && canHandoffCustody(data) && custody) {
            setConfirm({
                action,
                path: `/settings/apps/credential-custody/${custody.custody_uid}/handoff`,
                origin,
                body: {
                    expected_revision: custody.revision,
                    request_id: crypto.randomUUID(),
                },
            });
        } else if (action === "revoke" && custody?.state === "enabled" && data.approved_by_me) {
            setConfirm({
                action,
                path: `/settings/apps/credential-custody/${custody.custody_uid}/revoke`,
                origin,
                body: { expected_revision: custody.revision },
            });
        }
    };
    const submit = async () => {
        if (writing.current || blocked || !confirm) return;
        writing.current = true;
        setPending(true);
        const operation = confirm;
        setConfirm(null);
        try {
            // One-shot transport: an unknown credential handoff must never be replayed by auth recovery.
            const response = (
                await api.post<CustodyReceipt & HandoffReceipt & { destination_uid: string }>(operation.path, operation.body, {
                    env: { secretInput: true, interceptToast: true } as never,
                })
            ).data;
            const checked = await metadata.refetch();
            await service.refetch();
            const latest = checked.data;
            if (checked.isError || !latest) throw new Error("Custody readback unavailable");
            if (operation.action === "prepare") {
                if (latest.destination?.destination_uid !== response.destination_uid || latest.destination.revision !== response.revision)
                    throw new Error("Destination readback mismatch");
            } else if (operation.action === "handoff") {
                if (latest.last_handoff?.handoff_uid !== response.handoff_uid || latest.last_handoff.state !== response.state)
                    throw new Error("Handoff readback mismatch");
            } else if (
                latest.custody?.custody_uid !== response.custody_uid ||
                latest.custody.revision !== response.revision ||
                latest.custody.state !== response.state
            )
                throw new Error("Custody readback mismatch");
        } catch {
            // A read reconciles the original outcome; it cannot issue another credential.
            await metadata.refetch();
            setFailed(true);
        } finally {
            writing.current = false;
            setPending(false);
        }
    };
    const handoffState = data?.last_handoff?.state;
    const stateKey =
        handoffState === "delivered"
            ? "Custody handoff delivered"
            : handoffState === "prepared"
              ? "Custody handoff prepared"
              : handoffState === "unknown_revoked"
                ? "Custody handoff unknown"
                : handoffState === "revoked"
                  ? "Custody handoff revoked"
                  : "Custody handoff unavailable";
    return (
        <section className="space-y-3 rounded-lg border p-4" aria-busy={busy}>
            <h4 className="font-semibold">{t("project.settings.Service credential custody")}</h4>
            <p className="text-sm text-muted-foreground">{t("project.settings.Custody help")}</p>
            {(failed || metadata.isError) && <p role="alert">{t("project.settings.Custody refresh required")}</p>}
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void refresh()}>
                {t("common.Retry")}
            </Button>
            {data && (
                <div className="space-y-2 text-sm">
                    <p>
                        {t(
                            custody?.state === "enabled"
                                ? data.current
                                    ? "project.settings.Custody enabled"
                                    : "project.settings.Custody needs review"
                                : "project.settings.Custody not enabled"
                        )}
                    </p>
                    {data.destination && (
                        <p className="break-all">
                            {t("project.settings.Custody destination")}: {data.destination.origin}
                        </p>
                    )}
                    {data.last_handoff && <p role="status">{t(`project.settings.${stateKey}`)}</p>}
                    {data.last_handoff && data.last_handoff.custody_revision === custody?.revision && (
                        <p className="text-muted-foreground">{t("project.settings.Custody handoff already attempted")}</p>
                    )}
                </div>
            )}
            {service.isError ? (
                <p className="text-sm text-muted-foreground">{t("project.settings.Custody service unavailable")}</p>
            ) : (
                service.data && (
                    <p className="break-all text-sm">
                        {t("project.settings.Custody approved service")}: {service.data.template.origin}
                    </p>
                )
            )}
            <div className="flex flex-wrap gap-2">
                <Button
                    size="sm"
                    variant="outline"
                    disabled={blocked || !canConfigure || !service.data?.can_prepare || service.isError || !!confirm}
                    onClick={() => choose("prepare")}
                >
                    {t("project.settings.Use approved service")}
                </Button>
                <Button size="sm" disabled={blocked || !canConfigure || !data?.destination || !!confirm} onClick={() => choose("approve")}>
                    {t("project.settings.Approve credential custody")}
                </Button>
                {showHandoff && (
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={blocked || !canConfigure || !canHandoffCustody(data) || !!confirm}
                        onClick={() => choose("handoff")}
                    >
                        {t("project.settings.Send service credential")}
                    </Button>
                )}
                <Button
                    size="sm"
                    variant="outline"
                    disabled={blocked || custody?.state !== "enabled" || !data?.approved_by_me || !!confirm}
                    onClick={() => choose("revoke")}
                >
                    {t("project.settings.Revoke credential custody")}
                </Button>
            </div>
            {confirm && (
                <div className="space-y-3 rounded-md border p-3">
                    <p className="text-sm">{t(`project.settings.Custody confirm ${confirm.action}`)}</p>
                    {confirm.origin && <p className="break-all text-sm">{confirm.origin}</p>}
                    <div className="flex gap-2">
                        <Button size="sm" disabled={blocked} onClick={() => void submit()}>
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
