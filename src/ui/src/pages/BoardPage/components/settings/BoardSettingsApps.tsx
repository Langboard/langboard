import { lazy, Suspense, useState } from "react";
import { Blocks } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";
import { formatNumber } from "@/core/utils/LocaleFormat";
import useBoardAppCatalog, { useBoardAppCompositions, type CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { deletePanelState } from "@/core/apps/PanelSession";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import AppPackageIcon from "@/components/AppPackageIcon";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import BoardSettingsGitHub from "./BoardSettingsGitHub";
import BoardSettingsGlitchTip from "./BoardSettingsGlitchTip";
import BoardSettingsDokploy from "./BoardSettingsDokploy";
import BoardSettingsAppWorkflow from "./BoardSettingsAppWorkflow";
import BoardSettingsInboundConnections from "./BoardSettingsInboundConnections";
import BoardSettingsAppConsent from "./BoardSettingsAppConsent";
import BoardSettingsAppArchiveSync from "./BoardSettingsAppArchiveSync";
const AppPackagesPage = lazy(() => import("@/pages/SettingsPage/AppPackagesPage"));

export default function BoardSettingsApps() {
    const [t, i18n] = useTranslation();
    const { project, canEditBasicInfo, currentUser } = useBoardSettings();
    const compositionQuery = useBoardAppCompositions(project.uid, currentUser?.uid);
    // Modern hosts supply both in one request; old hosts use the flat fallback.
    const { data, isLoading, isError, refetch } = useBoardAppCatalog(project.uid, currentUser?.uid, compositionQuery.isError);
    const [compositionKey, setCompositionKey] = useState<string | null>(null);
    const [managedKey, setManagedKey] = useState<string | null>(null);
    const [section, setSection] = useState<"connections" | "permissions" | "workflow" | "panel">("connections");
    const [installing, setInstalling] = useState(false);
    // Group membership and module state must come from the same server snapshot.
    const snapshot = compositionQuery.isError ? undefined : compositionQuery.data;
    const apps = snapshot?.apps ?? data ?? [];
    const compositions = (snapshot?.compositions ?? []).filter(
        (group) => group.module_keys.length > 0 && group.module_keys.every((key) => apps.some((app) => app.key === key))
    );
    const composition = compositions.find((group) => group.key === compositionKey);
    const groupedKeys = new Set(compositions.flatMap((group) => group.module_keys));
    const visibleApps = composition
        ? composition.module_keys.flatMap((key) => apps.filter((app) => app.key === key))
        : apps.filter((app) => !groupedKeys.has(app.key));
    const refreshStore = async () => {
        const result = await compositionQuery.refetch();
        if (result.isError) return await refetch();
        return result;
    };
    const [panelTarget, setPanelTarget] = useState<string | null>(null);
    const [readSignals, setReadSignals] = useState(false);
    const [pending, setPending] = useState(false);
    const [disableTarget, setDisableTarget] = useState<string | null>(null);
    const [error, setError] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [connections, setConnections] = useState<CatalogApp | null>(null);
    const [selected, setSelected] = useState<CatalogApp | null>(null);
    const [disableGroup, setDisableGroup] = useState(false);
    const [storeFailed, setStoreFailed] = useState(false);
    const disableComposition = async () => {
        if (!composition || pending || storeFailed || !canEditBasicInfo) return;
        setPending(true);
        setError(false);
        try {
            await api.post(`/board/${project.uid}/settings/apps/compositions/${encodeURIComponent(composition.key)}/disable`, {
                expected_revision: composition.revision,
                binding_revisions: Object.fromEntries(visibleApps.map((app) => [app.key, app.binding?.revision ?? null])),
            });
            for (const app of visibleApps) {
                if (currentUser) deletePanelState(JSON.stringify([currentUser.uid, project.uid, app.key, app.version]));
            }
            setDisableGroup(false);
            const result = await refreshStore();
            if (result.isError) throw new Error("Store readback failed");
        } catch {
            setError(true);
            setStoreFailed(true);
        } finally {
            setPending(false);
        }
    };
    const disable = async (app: CatalogApp) => {
        if (pending || storeFailed || !canEditBasicInfo || !app.binding) return;
        setPending(true);
        setError(false);
        try {
            await api.post(`/board/${project.uid}/settings/apps/${app.key}/disable`, {
                binding_uid: app.binding.uid,
                expected_revision: app.binding.revision,
            });
            if (currentUser) deletePanelState(JSON.stringify([currentUser.uid, project.uid, app.key, app.version]));
            setDisableTarget(null);
            const result = await refreshStore();
            if (result.isError) throw new Error("Store readback failed");
        } catch {
            setStoreFailed(true);
            setError(true);
        } finally {
            setPending(false);
        }
    };
    const setPanel = async (app: CatalogApp, enabled: boolean) => {
        if (pending || storeFailed || !canEditBasicInfo || !app.app_revision) return;
        setPending(true);
        setError(false);
        try {
            await api.put(`/board/${project.uid}/settings/apps/${encodeURIComponent(app.key)}/panel`, {
                binding_uid: app.binding?.uid ?? null,
                expected_revision: app.binding?.revision ?? null,
                app_revision: app.app_revision,
                enabled,
                read_signals: enabled && app.capabilities.includes("signals.read") && !!app.signal_sources?.length && readSignals,
            });
            if (!enabled && currentUser) deletePanelState(JSON.stringify([currentUser.uid, project.uid, app.key, app.version]));
            setPanelTarget(null);
            const result = await refreshStore();
            if (result.isError) throw new Error("Store readback failed");
        } catch {
            setStoreFailed(true);
            setError(true);
        } finally {
            setPending(false);
        }
    };
    if (installing && currentUser)
        return (
            <div>
                <Button variant="ghost" onClick={() => setInstalling(false)}>
                    {t("project.settings.Back to App Store")}
                </Button>
                <Suspense fallback={<p role="status">{t("common.Loading")}</p>}>
                    <AppPackagesPage
                        currentUser={currentUser}
                        onInstalled={async () => {
                            const result = await refreshStore();
                            if (result.isError) throw new Error("Board catalog readback failed");
                        }}
                    />
                </Suspense>
            </div>
        );
    return (
        <div className="flex w-full flex-col gap-4 py-4">
            {connections ? (
                <>
                    <Button
                        size="sm"
                        variant="outline"
                        className="self-start"
                        disabled={dirty}
                        onClick={() => {
                            setConnections(null);
                            void refreshStore();
                        }}
                    >
                        {t("project.settings.Back to App Store")}
                    </Button>
                    <BoardSettingsInboundConnections key={connections.key} app={connections} onBusy={setDirty} />
                </>
            ) : selected ? (
                <>
                    <Button
                        size="sm"
                        variant="outline"
                        className="self-start"
                        disabled={dirty}
                        onClick={() => {
                            setSelected(null);
                            void refreshStore();
                        }}
                    >
                        {t("project.settings.Back to App Store")}
                    </Button>
                    <h3 className="text-base font-semibold">{selected.name}</h3>
                    <BoardSettingsAppWorkflow key={selected.key} appKey={selected.key} onDirtyChange={setDirty} />
                </>
            ) : (
                <>
                    {error && <p role="alert">{t("project.settings.App workflow save failed")}</p>}
                    {storeFailed && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={pending}
                            onClick={async () => {
                                const result = await refreshStore();
                                if (!result.isError) {
                                    setStoreFailed(false);
                                    setError(false);
                                    setDisableGroup(false);
                                }
                            }}
                        >
                            {t("common.Retry")}
                        </Button>
                    )}
                    <div className="flex items-center justify-between gap-3">
                        <h3 className="text-base font-semibold">{t("project.settings.App Store")}</h3>
                        {currentUser?.is_admin && (
                            <Button size="sm" variant="outline" disabled={pending || dirty} onClick={() => setInstalling(true)}>
                                {t("settings.Download and install")}
                            </Button>
                        )}
                    </div>
                    <p className="text-sm text-muted-foreground">{t("project.settings.App Store browse help")}</p>
                    {managedKey && (
                        <Button
                            size="sm"
                            variant="outline"
                            className="self-start"
                            disabled={pending || dirty}
                            onClick={() => {
                                setManagedKey(null);
                                setPanelTarget(null);
                                setDisableTarget(null);
                            }}
                        >
                            {t("project.settings.Back to App overview")}
                        </Button>
                    )}
                    {composition && (
                        <>
                            <Button
                                size="sm"
                                variant="outline"
                                className="self-start"
                                disabled={pending || dirty}
                                onClick={() => {
                                    setCompositionKey(null);
                                    setDisableGroup(false);
                                    setManagedKey(null);
                                    setPanelTarget(null);
                                    setDisableTarget(null);
                                }}
                            >
                                {t("project.settings.Back to App Store")}
                            </Button>
                            <h3 className="text-base font-semibold">{composition.name}</h3>
                            <p className="text-sm text-muted-foreground">{composition.description}</p>
                            {!managedKey && canEditBasicInfo && (
                                <div className="flex flex-col items-start gap-2">
                                    {disableGroup ? (
                                        <>
                                            <p className="text-sm text-muted-foreground">{t("project.settings.Disable App group help")}</p>
                                            <p className="text-xs text-muted-foreground">{composition.module_keys.join(" · ")}</p>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={pending || storeFailed}
                                                onClick={() => void disableComposition()}
                                            >
                                                {t("project.settings.Confirm disable App group")}
                                            </Button>
                                            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setDisableGroup(false)}>
                                                {t("common.Cancel")}
                                            </Button>
                                        </>
                                    ) : (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={pending || dirty || storeFailed}
                                            onClick={() => setDisableGroup(true)}
                                        >
                                            {t("project.settings.Disable App group")}
                                        </Button>
                                    )}
                                </div>
                            )}
                        </>
                    )}
                    <p className="text-sm text-muted-foreground">{t("project.settings.App Store help")}</p>
                    {(isLoading || compositionQuery.isLoading) && <p role="status">{t("common.Loading...")}</p>}
                    {compositionQuery.isError && !storeFailed && (
                        <div role="status" className="text-sm text-muted-foreground">
                            {t("project.settings.App grouping unavailable")}
                            <Button size="sm" variant="ghost" onClick={() => void compositionQuery.refetch()}>
                                {t("common.Retry")}
                            </Button>
                        </div>
                    )}
                    {isError && !storeFailed && (
                        <div role="alert">
                            {t("project.settings.App workflow unavailable")}
                            <Button size="sm" variant="outline" onClick={() => void refetch()}>
                                {t("common.Retry")}
                            </Button>
                        </div>
                    )}
                    <div className={managedKey ? "grid gap-3" : "grid gap-3 sm:grid-cols-2 xl:grid-cols-3"}>
                        {!composition &&
                            !managedKey &&
                            compositions.map((group) => (
                                <article
                                    key={`composition:${group.key}`}
                                    className="card card-border flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-5 shadow-sm"
                                >
                                    <div className="flex items-center gap-3">
                                        <span className="rounded-xl bg-muted p-3">
                                            <Blocks className="size-5 text-muted-foreground" aria-hidden="true" />
                                        </span>
                                        <h4 className="min-w-0 break-words font-semibold">{group.name}</h4>
                                    </div>
                                    <p className="my-3 flex-1 text-sm text-muted-foreground">{group.description}</p>
                                    <Button size="sm" variant="outline" onClick={() => setCompositionKey(group.key)}>
                                        {t("project.settings.Manage App modules")}
                                    </Button>
                                </article>
                            ))}
                        {visibleApps
                            .filter((app) => !managedKey || managedKey === app.key)
                            .map((app) => {
                                const { key, name, binding, workflow_requirements, resources } = app;
                                const panelEnabled = binding?.granted_capabilities.includes("panels.render");
                                const state = app.is_available === false ? "disabled" : (binding?.state ?? "unconfigured");
                                const workflowLabel = workflow_requirements ? "Configure workflow" : "App workflow contract pending";
                                return (
                                    <article
                                        key={key}
                                        className="card card-border flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-5 shadow-sm"
                                    >
                                        <div className="flex items-center gap-3">
                                            <AppPackageIcon appKey={key} installation={app.installation} />
                                            <h4 className="min-w-0 break-words font-semibold">{name}</h4>
                                        </div>
                                        {!managedKey ? (
                                            <>
                                                <span className="self-start rounded-md bg-muted px-2 py-1 text-xs">
                                                    {t(`project.settings.App state ${state}`)}
                                                </span>
                                                <p className="flex-1 text-sm text-muted-foreground">
                                                    {app.description ?? t(`project.settings.App ${key} summary`, { defaultValue: "" })}
                                                </p>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => {
                                                        setManagedKey(key);
                                                        setSection("connections");
                                                    }}
                                                >
                                                    {t("project.settings.Manage App")}
                                                </Button>
                                            </>
                                        ) : (
                                            <>
                                                <nav className="flex flex-wrap gap-1" aria-label={t("project.settings.App management sections")}>
                                                    {(["connections", "permissions", "workflow", "panel"] as const).map((value) => (
                                                        <Button
                                                            key={value}
                                                            size="sm"
                                                            variant={section === value ? "secondary" : "ghost"}
                                                            disabled={pending || dirty}
                                                            aria-pressed={section === value}
                                                            onClick={() => {
                                                                setSection(value);
                                                                setPanelTarget(null);
                                                                setDisableTarget(null);
                                                            }}
                                                        >
                                                            {t(`project.settings.App section ${value}`)}
                                                        </Button>
                                                    ))}
                                                </nav>
                                                {key === "github" && (
                                                    <div hidden={section !== "connections"}>
                                                        <BoardSettingsGitHub onStatusChange={() => void refreshStore()} />
                                                    </div>
                                                )}
                                                {key === "glitchtip" && (
                                                    <div hidden={section !== "connections"}>
                                                        <BoardSettingsGlitchTip onStatusChange={() => void refreshStore()} />
                                                    </div>
                                                )}
                                                {key === "dokploy" && (
                                                    <div hidden={section !== "connections"}>
                                                        <BoardSettingsDokploy onStatusChange={() => void refreshStore()} />
                                                    </div>
                                                )}
                                                <span className="self-start rounded-md bg-muted px-2 py-1 text-xs">
                                                    {t(`project.settings.App state ${state}`)}
                                                </span>
                                                {section === "connections" && resources && (
                                                    <details className="rounded-md border p-2 text-xs">
                                                        <summary className="cursor-pointer">
                                                            {t("project.settings.Selected App resources", { count: resources.selected_count })}
                                                        </summary>
                                                        <p className="mt-2 text-muted-foreground">
                                                            {t("project.settings.App stored resource status help")}
                                                        </p>
                                                        {resources.selected_count === 0 ? (
                                                            <p className="mt-2">{t("project.settings.No selected App resources")}</p>
                                                        ) : (
                                                            Object.entries({
                                                                access: resources.access_counts,
                                                                health: resources.health_counts,
                                                                connection: resources.connection_counts,
                                                            }).map(([category, counts]) => (
                                                                <div key={category} className="mt-2 flex flex-wrap gap-1">
                                                                    <span>{t(`project.settings.App resource ${category}`)}:</span>
                                                                    {Object.entries(counts).map(([state, count]) => (
                                                                        <span key={state} className="rounded bg-muted px-1.5">
                                                                            {t(`project.settings.App resource state ${state}`)} ·{" "}
                                                                            {formatNumber(count, i18n.language)}
                                                                        </span>
                                                                    ))}
                                                                </div>
                                                            ))
                                                        )}
                                                    </details>
                                                )}
                                                <p className="flex-1 text-sm text-muted-foreground">
                                                    {app.description ?? t(`project.settings.App ${key} summary`, { defaultValue: "" })}
                                                </p>
                                                {section === "permissions" && app.inbound_connection_management && (
                                                    <BoardSettingsAppConsent
                                                        key={`${app.key}:${app.app_revision}:${app.binding?.revision}`}
                                                        app={app}
                                                        onRefresh={refreshStore}
                                                    />
                                                )}
                                                {section === "workflow" && app.inbound_connection_management && (
                                                    <BoardSettingsAppArchiveSync
                                                        key={`archive:${app.key}:${app.app_revision}:${app.binding?.revision}`}
                                                        app={app}
                                                        onRefresh={refreshStore}
                                                    />
                                                )}
                                                {section === "connections" && app.inbound_connection_management && (
                                                    <Button size="sm" variant="outline" onClick={() => setConnections(app)}>
                                                        {t("project.settings.Manage inbound connections")}
                                                    </Button>
                                                )}
                                                {section === "connections" && !app.panel && !app.inbound_connection_management && (
                                                    <p className="text-xs text-muted-foreground">
                                                        {t("project.settings.App connection setup pending")}
                                                    </p>
                                                )}
                                                {section === "workflow" && (workflow_requirements || !app.panel) && (
                                                    <>
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            disabled={pending || app.is_available === false || !workflow_requirements}
                                                            onClick={() => setSelected(app)}
                                                        >
                                                            {t(`project.settings.${workflowLabel}`)}
                                                        </Button>
                                                    </>
                                                )}
                                                {section === "panel" &&
                                                    app.panel &&
                                                    app.capabilities.includes("panels.render") &&
                                                    (panelTarget === key ? (
                                                        <div className="flex flex-col gap-2 rounded-md border p-2">
                                                            <p className="text-sm">
                                                                {t("project.settings.App panel consent help", { name: app.panel.name })}
                                                            </p>
                                                            {app.capabilities.includes("signals.read") && (
                                                                <Checkbox
                                                                    checked={readSignals && !!app.signal_sources?.length}
                                                                    onCheckedChange={(checked) => setReadSignals(checked === true)}
                                                                    disabled={
                                                                        pending || storeFailed || !canEditBasicInfo || !app.signal_sources?.length
                                                                    }
                                                                    label={t("project.settings.Allow App signal reads")}
                                                                    description={t("project.settings.App signal source consent help", {
                                                                        sources:
                                                                            app.signal_sources?.join(", ") ||
                                                                            t("project.settings.App signal sources missing"),
                                                                    })}
                                                                />
                                                            )}
                                                            <Button
                                                                size="sm"
                                                                disabled={pending || storeFailed || !canEditBasicInfo || !app.app_revision}
                                                                onClick={() => void setPanel(app, true)}
                                                            >
                                                                {t("project.settings.Confirm App panel")}
                                                            </Button>
                                                            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setPanelTarget(null)}>
                                                                {t("common.Cancel")}
                                                            </Button>
                                                        </div>
                                                    ) : (
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            disabled={pending || storeFailed || !canEditBasicInfo || !app.app_revision}
                                                            onClick={() => {
                                                                if (panelEnabled) void setPanel(app, false);
                                                                else {
                                                                    // Preserve an existing independent signal grant when reopening
                                                                    // the panel editor; clearing it must be explicit.
                                                                    setReadSignals(binding?.granted_capabilities.includes("signals.read") ?? false);
                                                                    setPanelTarget(key);
                                                                }
                                                            }}
                                                        >
                                                            {t(`project.settings.${panelEnabled ? "Disable App panel" : "Review App panel"}`)}
                                                        </Button>
                                                    ))}
                                                {section === "permissions" &&
                                                    binding &&
                                                    binding.state !== "disabled" &&
                                                    (disableTarget === key ? (
                                                        <div className="flex flex-col gap-2">
                                                            <p className="text-xs">{t("project.settings.Disable App help")}</p>
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                disabled={!canEditBasicInfo || pending || storeFailed}
                                                                onClick={() => void disable(app)}
                                                            >
                                                                {t("project.settings.Confirm disable App")}
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                disabled={pending}
                                                                onClick={() => setDisableTarget(null)}
                                                            >
                                                                {t("common.Cancel")}
                                                            </Button>
                                                        </div>
                                                    ) : (
                                                        <Button
                                                            size="sm"
                                                            variant="ghost"
                                                            disabled={!canEditBasicInfo || pending || storeFailed}
                                                            onClick={() => setDisableTarget(key)}
                                                        >
                                                            {t("project.settings.Disable App")}
                                                        </Button>
                                                    ))}
                                            </>
                                        )}
                                    </article>
                                );
                            })}
                    </div>
                </>
            )}
        </div>
    );
}
