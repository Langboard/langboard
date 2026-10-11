import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Scope = "read" | "write" | "automation";
interface Consent {
    revision: number | null;
    app_revision: string;
    identity_revision: string;
    scopes: Scope[];
    available_scopes: Scope[];
    execution_supported: boolean;
}

export default function BoardSettingsMcpConsent({
    connectionUID,
    userUID,
    onBusy,
}: {
    connectionUID: string;
    userUID: string;
    onBusy: (busy: boolean) => void;
}) {
    const [t] = useTranslation();
    const [draft, setDraft] = useState<Scope[] | null>(null);
    const [failed, setFailed] = useState(false);
    const [pending, setPending] = useState(false);
    const writing = useRef(false);
    const path = `/settings/apps/connections/${encodeURIComponent(connectionUID)}/mcp-consent`;
    const config = { env: { interceptToast: true } as never };
    const query = useQuery({
        queryKey: ["app-mcp-consent", userUID, connectionUID],
        queryFn: async ({ signal }) => (await api.get<Consent>(path, { ...config, signal })).data,
        retry: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    useEffect(() => {
        onBusy(pending);
        return () => onBusy(false);
    }, [pending, onBusy]);
    const refresh = async () => {
        setPending(true);
        try {
            const result = await query.refetch();
            setFailed(result.isError);
            if (!result.isError) setDraft(null);
        } finally {
            setPending(false);
        }
    };
    const scopes = draft ?? query.data?.scopes ?? [];
    const disabled = pending || query.isFetching || query.isError || failed || !query.data;
    const save = async () => {
        if (disabled || writing.current || !query.data) return;
        writing.current = true;
        setPending(true);
        try {
            await api.put(
                path,
                {
                    scopes,
                    expected_revision: query.data.revision,
                    expected_app_revision: query.data.app_revision,
                    expected_identity_revision: query.data.identity_revision,
                },
                config
            );
            const result = await query.refetch();
            setFailed(result.isError);
            if (!result.isError) setDraft(null);
        } catch {
            setFailed(true);
        } finally {
            writing.current = false;
            setPending(false);
        }
    };
    return (
        <section className="space-y-3 rounded-md border p-3" aria-busy={pending || query.isFetching}>
            <h3 className="text-sm font-semibold">{t("project.settings.Connection MCP consent")}</h3>
            <p className="text-xs text-muted-foreground">{t("project.settings.Connection MCP consent help")}</p>
            {query.data?.execution_supported === false && (
                <p role="status" className="text-xs text-muted-foreground">
                    {t("project.settings.Connection MCP execution unavailable")}
                </p>
            )}
            {query.data?.available_scopes.map((scope) => (
                <Checkbox
                    key={scope}
                    label={t(`project.settings.Connection MCP ${scope}`)}
                    checked={scopes.includes(scope)}
                    disabled={disabled}
                    onCheckedChange={(checked) => setDraft(checked === true ? [...scopes, scope] : scopes.filter((value) => value !== scope))}
                />
            ))}
            {(failed || query.isError) && (
                <p role="alert" className="text-sm">
                    {t("project.settings.Connection MCP refresh required")}
                </p>
            )}
            <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={disabled} onClick={() => void save()}>
                    {t("project.settings.Save reviewed permissions")}
                </Button>
                <Button size="sm" variant="outline" disabled={disabled} onClick={() => setDraft([])}>
                    {t("project.settings.Clear App permissions")}
                </Button>
                <Button size="sm" variant="ghost" disabled={pending || query.isFetching} onClick={() => void refresh()}>
                    {t("common.Retry")}
                </Button>
            </div>
        </section>
    );
}
