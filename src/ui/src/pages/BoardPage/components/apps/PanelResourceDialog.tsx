import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { isAxiosError } from "axios";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { useManagedAppOrganizations } from "@/controllers/api/settings/apps/useAppGovernance";
import { ResourceSelectionConflict, type ResourceCandidate } from "@/core/apps/ResourceSelection";
import { InboundResources } from "../settings/BoardSettingsInboundResources";

export interface PanelResourceTarget {
    candidates: ResourceCandidate[];
    app_revision: string;
    binding_revision: string;
    signal: AbortSignal;
    isCurrent: () => boolean;
}

/** Native human configuration. App suggestions never provide a connection or a grant. */
export default function PanelResourceDialog({
    app,
    projectUID,
    userUID,
    target,
    onClose,
    onAuthorityDenied,
}: {
    app: CatalogApp;
    projectUID: string;
    userUID: string;
    target: PanelResourceTarget;
    onClose: () => void;
    onAuthorityDenied: () => void;
}) {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const openedSession = useRef(sessionVersion).current;
    const [scope, setScope] = useState("");
    const [cursor, setCursor] = useState<string>();
    const [organizationCursor, setOrganizationCursor] = useState<string>();
    const [connection, setConnection] = useState<string>();
    const [sending, setSending] = useState(false);
    const organizations = useManagedAppOrganizations(organizationCursor);
    const path = `/board/${projectUID}/apps/${encodeURIComponent(app.key)}/panel`;
    const revalidate = useCallback(
        async (signal: AbortSignal) => {
            signal.throwIfAborted();
            if (
                !target.isCurrent() ||
                useAuthStore.getState().currentUser?.uid !== userUID ||
                useAuthStore.getState().getSessionVersion() !== openedSession
            )
                throw new ResourceSelectionConflict("Panel identity changed");
            const config = { signal, env: { interceptToast: true } as never };
            const current = (await api.get<{ app_revision: string; binding_revision: string }>(path, config)).data;
            if (current.app_revision !== target.app_revision || current.binding_revision !== target.binding_revision)
                throw new ResourceSelectionConflict("Panel authority changed");
            // Existing native read verifies resources.read under the current panel grant.
            await api.post(
                `${path}/resources`,
                { app_revision: target.app_revision, binding_revision: target.binding_revision, cursor: null },
                config
            );
            signal.throwIfAborted();
        },
        [path, target, userUID, openedSession]
    );
    const connections = useQuery({
        queryKey: [
            "panel-resource-connections",
            userUID,
            sessionVersion,
            projectUID,
            app.key,
            scope,
            cursor,
            target.app_revision,
            target.binding_revision,
        ],
        queryFn: async ({ signal }) => {
            const current = AbortSignal.any([signal, target.signal]);
            await revalidate(current);
            return (
                await api.get<{ items: { connection_uid: string; state: string }[]; next_cursor: string | null }>(
                    `/settings/apps/registry/${encodeURIComponent(app.key)}/inbound-connections`,
                    { signal: current, params: { organization_uid: scope || undefined, after: cursor }, env: { interceptToast: true } as never }
                )
            ).data;
        },
        enabled: actor === userUID && !target.signal.aborted,
        retry: 0,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    useEffect(() => {
        const close = () => onClose();
        target.signal.addEventListener("abort", close, { once: true });
        if (target.signal.aborted || actor !== userUID || sessionVersion !== openedSession) close();
        return () => target.signal.removeEventListener("abort", close);
    }, [target, actor, userUID, sessionVersion, openedSession, onClose]);
    useEffect(() => {
        if (
            connections.error instanceof ResourceSelectionConflict ||
            (isAxiosError(connections.error) && [401, 403, 404, 409].includes(connections.error.response?.status ?? 0))
        )
            onAuthorityDenied();
    }, [connections.error, onAuthorityDenied]);
    return (
        <Dialog.Root
            open
            onOpenChange={(open) => {
                if (!open && !sending) onClose();
            }}
        >
            <Dialog.Content className="max-w-lg" withCloseButton={!sending} disableOverlayClick={sending}>
                <Dialog.Header>
                    <Dialog.Title>{t("project.settings.Manage service resources")}</Dialog.Title>
                    <Dialog.Description>{t("project.settings.Service resource help")}</Dialog.Description>
                </Dialog.Header>
                <select
                    aria-label={t("project.settings.Connection ownership")}
                    value={scope}
                    disabled={sending}
                    onChange={(event) => {
                        setScope(event.target.value);
                        setCursor(undefined);
                        setConnection(undefined);
                    }}
                >
                    <option value="">{t("project.settings.Personal connection")}</option>
                    {organizations.data?.items.map((item) => (
                        <option key={item.uid} value={item.uid}>
                            {item.name}
                        </option>
                    ))}
                </select>
                {organizations.data?.next_cursor && (
                    <Button size="sm" disabled={sending} onClick={() => setOrganizationCursor(organizations.data!.next_cursor!)}>
                        {t("settings.App more organizations")}
                    </Button>
                )}
                <Button
                    size="sm"
                    disabled={sending || connections.isFetching}
                    onClick={() => {
                        setConnection(undefined);
                        void connections.refetch();
                    }}
                >
                    {t("common.Retry")}
                </Button>
                {connections.isFetching && <p role="status">{t("common.Loading...")}</p>}
                {connections.isError && <p role="alert">{t("project.settings.Inbound connection refresh required")}</p>}
                {!connections.isError &&
                    !connections.isFetching &&
                    connections.data?.items.map((item) => (
                        <Button
                            key={item.connection_uid}
                            size="sm"
                            disabled={sending || item.state !== "connected"}
                            aria-pressed={connection === item.connection_uid}
                            onClick={() => setConnection(item.connection_uid)}
                        >
                            {item.connection_uid}
                        </Button>
                    ))}
                {connections.data?.next_cursor && (
                    <Button
                        size="sm"
                        disabled={sending || connections.isFetching}
                        onClick={() => {
                            setConnection(undefined);
                            setCursor(connections.data!.next_cursor!);
                        }}
                    >
                        {t("project.settings.More inbound connections")}
                    </Button>
                )}
                {connection && !connections.isError && !connections.isFetching && (
                    <InboundResources
                        key={`${userUID}/${sessionVersion}/${scope}/${connection}`}
                        app={app}
                        projectUID={projectUID}
                        userUID={userUID}
                        connectionUID={connection}
                        canEditBasicInfo={actor === userUID && !target.signal.aborted}
                        onBusy={setSending}
                        suggestions={target.candidates}
                        authority={{
                            ...target,
                            revalidate,
                            onDenied: onAuthorityDenied,
                            isCurrent: () =>
                                target.isCurrent() &&
                                useAuthStore.getState().currentUser?.uid === userUID &&
                                useAuthStore.getState().getSessionVersion() === openedSession,
                        }}
                    />
                )}
            </Dialog.Content>
        </Dialog.Root>
    );
}
