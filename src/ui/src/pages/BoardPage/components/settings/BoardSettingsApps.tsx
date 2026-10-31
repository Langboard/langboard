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
import BoardSettingsAppWorkflow from "./BoardSettingsAppWorkflow";
import BoardSettingsInboundConnections from "./BoardSettingsInboundConnections";
import BoardSettingsAppConsent from "./BoardSettingsAppConsent";
import BoardSettingsAppArchiveSync from "./BoardSettingsAppArchiveSync";
const AppPackagesPage = lazy(() => import("@/pages/SettingsPage/AppPackagesPage"));
const BoardSettingsGitHub = lazy(() => import("./BoardSettingsGitHub"));
const BoardSettingsGlitchTip = lazy(() => import("./BoardSettingsGlitchTip"));
const BoardSettingsDokploy = lazy(() => import("./BoardSettingsDokploy"));
const connectionForms = new Map<string, typeof BoardSettingsGitHub>([
    ["github", BoardSettingsGitHub],
    ["glitchtip", BoardSettingsGlitchTip],
    ["dokploy", BoardSettingsDokploy],
]);

export default function BoardSettingsApps() {
    const [t, i18n] = useTranslation();
    const { project, canEditBasicInfo, currentUser } = useBoardSettings();
    const compositionQuery = useBoardAppCompositions(project.uid, currentUser?.uid);
    // Modern hosts supply both in one request; old hosts use the flat fallback.
    const { data, isLoading, isError, refetch } = useBoardAppCatalog(project.uid, currentUser?.uid, compositionQuery.isError);
    const [compositionKey, setCompositionKey] = useState<string | null>(null);
    const [managedKey, setManagedKey] = useState<string | null>(null);
    const [section, setSection] = useState<"connections" | "permissions" | "workflow" | "panel">("connections");
    const [visitedModules, setVisitedModules] = useState<string[]>([]);
    const [visitedSections, setVisitedSections] = useState<Record<string, string[]>>({});
    const [moduleSections, setModuleSections] = useState<Record<string, typeof section>>({});
    const openModule = (key: string) => {
        setManagedKey(key);
        setVisitedSections((sections) => ({ ...sections, [key]: sections[key] ?? ["connections"] }));
        setSection(moduleSections[key] ?? "connections");
        setVisitedModules((keys) => (keys.includes(key) ? keys : [...keys, key]));
        setPanelTarget(null);
        setDisableTarget(null);
    };
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
    const [connections, setConnections] = useState<string[]>([]);
    const [selected, setSelected] = useState<string[]>([]);
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
        if (pending || storeFailed || !canEditBasicInfo || !app.app_revision || (enabled && app.is_available === false)) return;
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
                        projectUid={project.uid}
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
                    {currentUser && (
                        <Button size="sm" variant="outline" disabled={pending || dirty} onClick={() => setInstalling(true)}>
                            {t(currentUser.is_admin ? "settings.Download and install" : "settings.Request installation")}
                        </Button>
                    )}
                </div>
                <p className="text-sm text-muted-foreground">{t("project.settings.App Store browse help")}</p>
                {managedKey && !composition && (
                    <Button
                        size="sm"
                        variant="outline"
                        className="self-start"
                        disabled={pending || dirty}
                        onClick={() => {
                            setManagedKey(null);
                            setVisitedModules([]);
                            setVisitedSections({});
                            setConnections([]);
                            setSelected([]);
                            setModuleSections({});
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
                                setVisitedModules([]);
                                setVisitedSections({});
                                setConnections([]);
                                setSelected([]);
                                setModuleSections({});
                                setPanelTarget(null);
                                setDisableTarget(null);
                            }}
                        >
                            {t("project.settings.Back to App Store")}
                        </Button>
                        <h3 className="text-base font-semibold">{composition.name}</h3>
                        <p className="text-sm text-muted-foreground">{composition.description}</p>
                        <nav className="flex flex-wrap gap-2" aria-label={t("project.settings.Manage App modules")}>
                            {visibleApps.map((app) => (
                                <Button
                                    key={app.key}
                                    size="sm"
                                    variant={managedKey === app.key ? "secondary" : "outline"}
                                    aria-pressed={managedKey === app.key}
                                    disabled={pending || dirty}
                                    onClick={() => openModule(app.key)}
                                >
                                    {app.name}
                                </Button>
                            ))}
                        </nav>
                        {canEditBasicInfo && (
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
                                <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => {
                                        setCompositionKey(group.key);
                                        openModule(group.module_keys[0]);
                                    }}
                                >
                                    {t("project.settings.Manage App modules")}
                                </Button>
                            </article>
                        ))}
                    {visibleApps
                        .filter((app) => !managedKey || managedKey === app.key || visitedModules.includes(app.key))
                        .map((app) => {
                            const { key, name, binding, workflow_requirements, resources } = app;
                            const ConnectionForm = connectionForms.get(key);
                            const activeSection = managedKey === key ? section : (moduleSections[key] ?? "connections");
                            const panelEnabled = binding?.granted_capabilities.includes("panels.render");
                            const state = app.is_available === false ? "disabled" : (binding?.state ?? "unconfigured");
                            const description =
                                app.description?.trim() ||
                                t(`project.settings.App ${key} summary`, { defaultValue: "" }) ||
                                t("project.settings.App generic summary");
                            const workflowLabel = workflow_requirements ? "Configure workflow" : "App workflow contract pending";
                            return (
                                <div key={key} hidden={!!managedKey && managedKey !== key}>
                                    <article className="card card-border flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-5 shadow-sm">
                                        <div className="flex items-center gap-3">
                                            <AppPackageIcon appKey={key} installation={app.installation} />
                                            <h4 className="min-w-0 break-words font-semibold">{name}</h4>
                                        </div>
                                        {!managedKey ? (
                                            <>
                                                <span className="self-start rounded-md bg-muted px-2 py-1 text-xs">
                                                    {t(`project.settings.App state ${state}`)}
                                                </span>
                                                <p className="flex-1 text-sm text-muted-foreground">{description}</p>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => {
                                                        openModule(key);
                                                    }}
                                                >
                                                    {t("project.settings.Manage App")}
                                                </Button>
                                            </>
                                        ) : (
                                            <>
                                                <p className="break-all text-xs text-muted-foreground">
                                                    {key} · {app.version}
                                                </p>
                                                <nav className="flex flex-wrap gap-1" aria-label={t("project.settings.App management sections")}>
                                                    {(["connections", "permissions", "workflow", "panel"] as const).map((value) => (
                                                        <Button
                                                            key={value}
                                                            size="sm"
                                                            variant={activeSection === value ? "secondary" : "ghost"}
                                                            disabled={pending || dirty}
                                                            aria-pressed={activeSection === value}
                                                            onClick={() => {
                                                                setSection(value);
                                                                setVisitedSections((sections) => ({
                                                                    ...sections,
                                                                    [key]: [...new Set([...(sections[key] ?? []), value])],
                                                                }));
                                                                setModuleSections((sections) => ({ ...sections, [key]: value }));
                                                                setPanelTarget(null);
                                                                setDisableTarget(null);
                                                            }}
                                                        >
                                                            {t(`project.settings.App section ${value}`)}
                                                        </Button>
                                                    ))}
                                                </nav>
                                                <p className="text-sm text-muted-foreground">
                                                    {t(`project.settings.App section ${activeSection} help`)}
                                                </p>
                                                {ConnectionForm && visitedSections[key]?.includes("connections") && (
                                                    <div hidden={activeSection !== "connections"}>
                                                        <Suspense fallback={<p role="status">{t("common.Loading...")}</p>}>
                                                            <ConnectionForm onStatusChange={() => void refreshStore()} />
                                                        </Suspense>
                                                    </div>
                                                )}
                                                <span className="self-start rounded-md bg-muted px-2 py-1 text-xs">
                                                    {t(`project.settings.App state ${state}`)}
                                                </span>
                                                {activeSection === "connections" && resources && (
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
                                                <p className="flex-1 text-sm text-muted-foreground">{description}</p>
                                                {activeSection === "permissions" && (
                                                    <div className="flex flex-wrap gap-2 text-xs">
                                                        {(binding?.granted_capabilities ?? []).map((capability) => (
                                                            <code key={capability}>{capability}</code>
                                                        ))}
                                                    </div>
                                                )}
                                                {visitedSections[key]?.includes("permissions") && app.inbound_connection_management && (
                                                    <div hidden={activeSection !== "permissions"}>
                                                        <BoardSettingsAppConsent
                                                            key={`${app.key}:${app.app_revision}:${app.binding?.revision}`}
                                                            app={app}
                                                            onRefresh={refreshStore}
                                                        />
                                                    </div>
                                                )}
                                                {visitedSections[key]?.includes("workflow") && app.inbound_connection_management && (
                                                    <div hidden={activeSection !== "workflow"}>
                                                        <BoardSettingsAppArchiveSync
                                                            key={`archive:${app.key}:${app.app_revision}:${app.binding?.revision}`}
                                                            app={app}
                                                            onRefresh={refreshStore}
                                                        />
                                                    </div>
                                                )}
                                                {activeSection === "connections" && app.inbound_connection_management && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => setConnections((keys) => [...new Set([...keys, key])])}
                                                    >
                                                        {t("project.settings.Manage inbound connections")}
                                                    </Button>
                                                )}
                                                {connections.includes(key) && (
                                                    <div hidden={activeSection !== "connections"}>
                                                        <BoardSettingsInboundConnections key={key} app={app} onBusy={setDirty} />
                                                    </div>
                                                )}
                                                {activeSection === "connections" &&
                                                    !ConnectionForm &&
                                                    !app.panel &&
                                                    !app.inbound_connection_management && (
                                                        <p className="text-xs text-muted-foreground">
                                                            {t("project.settings.App connection setup pending")}
                                                        </p>
                                                    )}
                                                {activeSection === "workflow" && (workflow_requirements || !app.panel) && (
                                                    <>
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            disabled={pending || dirty || app.is_available === false || !workflow_requirements}
                                                            onClick={() => setSelected((keys) => [...new Set([...keys, key])])}
                                                        >
                                                            {t(`project.settings.${workflowLabel}`)}
                                                        </Button>
                                                        {selected.includes(key) && (
                                                            <BoardSettingsAppWorkflow key={key} appKey={key} onDirtyChange={setDirty} />
                                                        )}
                                                        {selected.includes(key) && (
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                disabled={dirty}
                                                                onClick={() => setSelected((keys) => keys.filter((item) => item !== key))}
                                                            >
                                                                {t("project.settings.Back to App Store")}
                                                            </Button>
                                                        )}
                                                    </>
                                                )}
                                                {activeSection === "panel" &&
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
                                                                disabled={
                                                                    pending ||
                                                                    storeFailed ||
                                                                    !canEditBasicInfo ||
                                                                    !app.app_revision ||
                                                                    app.is_available === false
                                                                }
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
                                                            disabled={
                                                                pending ||
                                                                storeFailed ||
                                                                !canEditBasicInfo ||
                                                                !app.app_revision ||
                                                                (!panelEnabled && app.is_available === false)
                                                            }
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
                                                {activeSection === "permissions" &&
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
                                </div>
                            );
                        })}
                </div>
            </>
        </div>
    );
}
