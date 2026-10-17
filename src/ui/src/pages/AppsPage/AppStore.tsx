import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowUp, ArrowDown, ChevronRight, Pin, X } from "lucide-react";
import AppPackageIcon, { type PackageInstallation } from "@/components/AppPackageIcon";
import Button from "@/components/base/Button";
import Loading from "@/components/base/Loading";
import { DashboardStyledLayout } from "@/components/Layout";
import { api } from "@/core/helpers/Api";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import usePinnedAppRail from "@/core/apps/usePinnedAppRail";
import { MAX_PINNED_APPS } from "@/core/stores/UserSettingsStore";

interface CatalogApp {
    key: string;
    name: string;
    description: string;
    version?: string;
    capabilities?: string[];
    is_installed?: boolean;
    repository?: string;
    source?: "official" | "manual" | "registered";
    installation?: PackageInstallation | null;
}
interface Catalog {
    items: CatalogApp[];
    official: CatalogApp[];
    next_cursor: string | null;
    effective_mode: string;
    can_manage_installation: boolean;
}

export default function AppStore({ userUID }: { userUID: string }) {
    const [t] = useTranslation();
    const { appKey } = useParams();
    const [search] = useSearchParams();
    const navigate = usePageNavigateRef();
    const projectUID = search.get("project_uid") ?? undefined;
    const pinnedApps = usePinnedAppRail({ userUID, projectUID });
    const after = appKey ? undefined : (search.get("after") ?? undefined);
    const valid = (!appKey || /^[a-z][a-z0-9_-]{0,31}$/.test(appKey)) && (!projectUID || /^[A-Za-z0-9]{11}$/.test(projectUID));
    const query = useQuery({
        queryKey: ["user-app-catalog", userUID, projectUID, appKey, after],
        enabled: valid,
        queryFn: async ({ signal }) =>
            (await api.get<Catalog>("/apps/catalog", { signal, params: { project_uid: projectUID, key: appKey, after } })).data,
        retry: 0,
        gcTime: 0,
    });
    const suffix = search.size ? `?${search}` : "";
    const catalog = query.data;
    const apps = new Map<string, CatalogApp>();
    for (const app of catalog?.official ?? []) apps.set(app.key, app);
    for (const app of catalog?.items ?? []) apps.set(app.key, { ...apps.get(app.key), ...app });
    const selected = appKey ? apps.get(appKey) : undefined;
    const isPinned = pinnedApps.pins.some((pin) => pin.key === selected?.key);
    const listURL = `${ROUTES.APPS.ROUTE}${suffix}`;
    const nextSearch = new URLSearchParams({ ...(projectUID ? { project_uid: projectUID } : {}), after: catalog?.next_cursor ?? "" });
    const icon = (app: CatalogApp) => (
        <AppPackageIcon
            appKey={app.key}
            installation={app.installation}
            fallbackUrl={
                app.repository && app.source !== "manual" && app.source !== "registered"
                    ? `https://github.com/${app.repository}/releases/latest/download/icon-light.svg`
                    : undefined
            }
            fallbackDarkUrl={
                app.repository && app.source !== "manual" && app.source !== "registered"
                    ? `https://github.com/${app.repository}/releases/latest/download/icon-dark.svg`
                    : undefined
            }
        />
    );
    return (
        <DashboardStyledLayout
            headerNavs={[]}
            headerTitle={t("settings.App store")}
            activityRailItems={[
                { icon: "folder-kanban", label: t("dashboard.Projects"), mobile: "primary", onClick: () => navigate(ROUTES.DASHBOARD.PROJECTS.ALL) },
                ...(projectUID
                    ? [
                          {
                              icon: "columns-3",
                              label: t("appStore.Board"),
                              mobile: "primary" as const,
                              onClick: () => navigate(ROUTES.BOARD.MAIN(projectUID)),
                          },
                      ]
                    : []),
                ...pinnedApps.items,
                { icon: "plus", label: t("settings.App store"), mobile: "add", active: true, onClick: () => navigate(listURL) },
                { icon: "settings", label: t("settings.App settings"), placement: "bottom", onClick: () => navigate(ROUTES.SETTINGS.ROUTE) },
            ]}
        >
            <section className="mx-auto w-full max-w-4xl space-y-5 p-4 sm:p-6" aria-label={t("settings.App store")}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <h1 className="text-2xl font-semibold">{t("settings.App store")}</h1>
                    {catalog?.can_manage_installation && (
                        <Button asChild variant="outline" className="min-h-11">
                            <Link to={ROUTES.SETTINGS.APP_PACKAGES}>{t("appStore.Manage installation")}</Link>
                        </Button>
                    )}
                </div>
                {!!pinnedApps.pins.length && (
                    <section aria-label={t("appStore.Your shortcuts")} className="rounded-xl border bg-card p-4">
                        <h2 className="mb-3 font-semibold">{t("appStore.Your shortcuts")}</h2>
                        <p className="mb-3 text-sm text-muted-foreground">{t("appStore.Shortcut storage")}</p>
                        <ol className="space-y-2">
                            {pinnedApps.pins.map((pin, index) => (
                                <li key={pin.key} className="flex min-w-0 items-center gap-1">
                                    <Link
                                        className="min-w-0 flex-1 truncate rounded p-2 text-sm hover:bg-accent"
                                        to={`${ROUTES.APPS.DETAIL(pin.key)}${suffix}`}
                                    >
                                        {pin.name}
                                    </Link>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="min-h-11 min-w-11"
                                        disabled={pinnedApps.busy || index === 0}
                                        aria-label={t("appStore.Earlier shortcut", { name: pin.name })}
                                        onClick={() => pinnedApps.move(pin.key, -1)}
                                    >
                                        <ArrowUp className="size-4" />
                                    </Button>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="min-h-11 min-w-11"
                                        disabled={pinnedApps.busy || index === pinnedApps.pins.length - 1}
                                        aria-label={t("appStore.Later shortcut", { name: pin.name })}
                                        onClick={() => pinnedApps.move(pin.key, 1)}
                                    >
                                        <ArrowDown className="size-4" />
                                    </Button>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="min-h-11 min-w-11"
                                        aria-label={t("appStore.Remove shortcut", { name: pin.name })}
                                        disabled={pinnedApps.busy}
                                        onClick={() => pinnedApps.toggle(pin)}
                                    >
                                        <X className="size-4" />
                                    </Button>
                                </li>
                            ))}
                        </ol>
                    </section>
                )}
                {pinnedApps.error && (
                    <div role="alert" className="space-y-2 rounded-xl border p-3 text-sm">
                        <p>{t("appStore.Shortcut sync unavailable")}</p>
                        <Button variant="outline" className="min-h-11" onClick={pinnedApps.retry}>
                            {t("common.Retry")}
                        </Button>
                    </div>
                )}
                {!valid || query.isError ? (
                    <div role="alert" className="rounded-xl border p-4">
                        <p>{t("appStore.Catalog unavailable")}</p>
                        {valid && (
                            <Button className="mt-3 min-h-11" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
                                {t("common.Retry")}
                            </Button>
                        )}
                    </div>
                ) : !catalog ? (
                    <div role="status">
                        <Loading variant="secondary" size="2" animate="pulse" />
                    </div>
                ) : catalog.effective_mode === "disabled" ? (
                    <p role="status">{t("appStore.Disabled by policy")}</p>
                ) : appKey ? (
                    selected ? (
                        <article className="space-y-5 rounded-xl border bg-card p-5">
                            <Button asChild variant="ghost" className="min-h-11">
                                <Link to={listURL}>
                                    <ArrowLeft className="mr-2 size-4" />
                                    {t("appStore.All apps")}
                                </Link>
                            </Button>
                            <div className="flex items-center gap-4">
                                {icon(selected)}
                                <div className="min-w-0">
                                    <h2 className="break-words text-xl font-semibold">{selected.name}</h2>
                                    <p className="text-sm text-muted-foreground">
                                        {selected.version ?? t("appStore.Not installed")} ·{" "}
                                        {t(
                                            selected.source === "manual"
                                                ? "appStore.Manual app"
                                                : selected.source === "registered"
                                                  ? "appStore.Registered app"
                                                  : "appStore.Official app"
                                        )}
                                    </p>
                                </div>
                            </div>
                            <p className="break-words text-sm text-muted-foreground">{selected.description}</p>
                            {!!selected.capabilities?.length && (
                                <div>
                                    <h3 className="mb-2 font-medium">{t("appStore.Requested capabilities")}</h3>
                                    <ul className="space-y-1 text-sm text-muted-foreground">
                                        {selected.capabilities.map((scope) => (
                                            <li key={scope} className="break-all">
                                                {scope}
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {selected.is_installed && (
                                <div className="space-y-2">
                                    <Button
                                        variant="outline"
                                        className="min-h-11"
                                        aria-pressed={isPinned}
                                        disabled={pinnedApps.busy || (!isPinned && pinnedApps.pins.length >= MAX_PINNED_APPS)}
                                        onClick={() => pinnedApps.toggle(selected)}
                                    >
                                        <Pin className="mr-2 size-4" />
                                        {t(isPinned ? "appStore.Unpin from rail" : "appStore.Pin to rail")}
                                    </Button>
                                    {!isPinned && pinnedApps.pins.length >= MAX_PINNED_APPS && (
                                        <p className="text-sm text-muted-foreground">{t("appStore.Shortcut limit", { count: MAX_PINNED_APPS })}</p>
                                    )}
                                </div>
                            )}
                            <p className="text-sm text-muted-foreground">{t("appStore.Separate consent")}</p>
                            {selected.is_installed && projectUID ? (
                                <Button asChild className="min-h-11">
                                    <Link to={ROUTES.BOARD.SETTINGS_PAGE(projectUID, "apps")}>{t("appStore.Configure on board")}</Link>
                                </Button>
                            ) : (
                                <p className="text-sm">
                                    {t(selected.is_installed ? "appStore.Choose board" : "appStore.Administrator installation required")}
                                </p>
                            )}
                        </article>
                    ) : (
                        <p role="status">{t("appStore.App unavailable")}</p>
                    )
                ) : (
                    <>
                        <p className="text-sm text-muted-foreground">{t("appStore.Discovery description")}</p>
                        <div className="grid gap-3 md:grid-cols-2">
                            {[...apps.values()].map((app) => (
                                <Link
                                    key={app.key}
                                    to={`${ROUTES.APPS.DETAIL(app.key)}${suffix}`}
                                    className={
                                        "flex min-h-24 min-w-0 items-center gap-4 rounded-xl border bg-card p-4 transition-colors " +
                                        "hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    }
                                >
                                    {icon(app)}
                                    <div className="min-w-0 flex-1">
                                        <h2 className="truncate font-semibold">{app.name}</h2>
                                        <p className="mt-1 line-clamp-1 text-sm text-muted-foreground">{app.description}</p>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {t(app.is_installed ? "appStore.Installed" : "appStore.Not installed")}
                                        </p>
                                    </div>
                                    <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                                </Link>
                            ))}
                        </div>
                        {!apps.size && <p role="status">{t("appStore.No apps")}</p>}
                        {catalog.next_cursor && (
                            <Button asChild variant="outline" className="min-h-11">
                                <Link to={`${ROUTES.APPS.ROUTE}?${nextSearch}`}>{t("appStore.Next page")}</Link>
                            </Button>
                        )}
                    </>
                )}
            </section>
        </DashboardStyledLayout>
    );
}
