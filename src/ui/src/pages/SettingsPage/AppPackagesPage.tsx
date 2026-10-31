import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import type { AuthUser } from "@/core/models";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Download, Package, Search, ShieldCheck, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Input from "@/components/base/Input";
import AppPackageIcon, { packageAsset, type PackageInstallation } from "@/components/AppPackageIcon";
import SettingsLoadState from "./SettingsLoadState";
import AppInstallationRequests from "./AppInstallationRequests";

interface InstalledApp {
    declaration: { key: string; name: string; version: string; description: string; capabilities?: string[] };
    is_enabled: boolean;
    revision: string;
    installation?: PackageInstallation | null;
}
interface OfficialApp {
    key: string;
    repository: string;
    name: string;
    description: string;
    icon_url?: string;
    icon_dark_url?: string;
}
const root = "/settings/apps/packages";

export default function AppPackagesPage({
    currentUser,
    onInstalled,
    projectUid,
}: {
    currentUser: AuthUser.TModel;
    onInstalled?: () => Promise<unknown>;
    projectUid?: string;
}) {
    const [t] = useTranslation();
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<"official" | "installed" | "manual">("official");
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    const [manualOpen, setManualOpen] = useState(false);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const [updateKey, setUpdateKey] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);
    const invalidFile = !!file && (!file.size || file.size > 8 * 1024 * 1024);
    const query = useQuery({
        queryKey: ["app-packages", currentUser.uid],
        enabled: currentUser.is_admin,
        queryFn: async ({ signal }) => (await api.get<{ installed: InstalledApp[]; official: OfficialApp[] }>(root, { signal })).data,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const install = async (key?: string) => {
        if (pending || failed || !currentUser.is_admin) return;
        setPending(true);
        try {
            let receipt: InstalledApp;
            if (key) {
                const previous = query.data?.installed.find((app) => app.declaration.key === key);
                receipt = (
                    await api.post<{ app: InstalledApp }>(`${root}/official/${encodeURIComponent(key)}/install`, {
                        expected_revision: previous?.revision ?? null,
                    })
                ).data.app;
            } else {
                if (!file || invalidFile) throw new Error("Invalid ZIP package");
                const form = new FormData();
                form.append("file", file);
                if (updateKey) {
                    const previous = query.data?.installed.find((app) => app.declaration.key === updateKey);
                    if (!previous) throw new Error("Update target unavailable");
                    form.append("expected_revision", previous.revision);
                }
                receipt = (await api.post<{ app: InstalledApp }>(`${root}/upload`, form)).data.app;
            }
            if (!receipt?.declaration?.key || !/^[0-9a-f]{64}$/.test(receipt.revision)) throw new Error("Invalid installation receipt");
            if ((key || updateKey) && receipt.declaration.key !== (key || updateKey)) throw new Error("Installation target mismatch");
            const refreshed = await query.refetch();
            if (refreshed.isError) throw new Error("Installation readback unavailable");
            const installed = refreshed.data?.installed.find((app) => app.declaration.key === receipt.declaration.key);
            if (installed?.revision !== receipt.revision || !installed.is_enabled) throw new Error("Installation readback mismatch");
            await onInstalled?.();
            setFile(null);
            if (fileInput.current) fileInput.current.value = "";
        } catch {
            setFailed(true);
        } finally {
            setPending(false);
        }
    };
    if (!currentUser.is_admin) return <AppInstallationRequests currentUser={currentUser} projectUid={projectUid} />;
    if (!query.data) return <SettingsLoadState error={query.isError} isFetching={query.isFetching} retry={() => void query.refetch()} />;
    const installed = query.data.installed;
    const catalog =
        filter === "official"
            ? query.data.official.map((item) => ({ ...item, installed: installed.find((app) => app.declaration.key === item.key) }))
            : installed
                  .filter((app) => filter !== "manual" || app.installation?.kind !== "official")
                  .map((app) => ({
                      key: app.declaration.key,
                      name: app.declaration.name,
                      description: app.declaration.description,
                      repository: app.installation?.repository ?? "",
                      icon_url: undefined,
                      icon_dark_url: undefined,
                      installed: app,
                  }));
    const visible = catalog.filter((item) => `${item.name} ${item.description}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
    const detail = catalog.find((item) => item.key === selectedKey);
    const errorBanner = failed && (
        <div role="alert" className="rounded-xl border p-4">
            <p className="text-sm">{t("settings.App installation uncertain")}</p>
            <Button
                className="mt-2"
                variant="outline"
                size="sm"
                disabled={pending || query.isFetching}
                onClick={async () => {
                    const result = await query.refetch();
                    if (!result.isError) setFailed(false);
                }}
            >
                {t("common.Refresh")}
            </Button>
        </div>
    );
    const installButton = (key: string, previous?: InstalledApp) => (
        <Button className="rounded-full" variant="outline" disabled={pending || failed} onClick={() => void install(key)}>
            <Download className="mr-2 size-4" />
            {t(pending ? "settings.Installing app" : previous ? "settings.Update app" : "settings.Download and install")}
        </Button>
    );
    return (
        <section className="mx-auto w-full max-w-6xl space-y-6 p-4 sm:p-6" aria-label={t("settings.App store")}>
            <AppInstallationRequests currentUser={currentUser} projectUid={projectUid} />
            {detail ? (
                <>
                    <Button className="text-foreground" variant="ghost" size="sm" onClick={() => setSelectedKey(null)}>
                        <ArrowLeft className="mr-2 size-4" />
                        {t("settings.App store")}
                    </Button>
                    <header className="flex flex-wrap items-center gap-4 rounded-3xl border bg-card p-5 sm:p-8">
                        <AppPackageIcon
                            appKey={detail.key}
                            installation={detail.installed?.installation}
                            fallbackUrl={detail.icon_url}
                            fallbackDarkUrl={detail.icon_dark_url}
                        />
                        <div className="min-w-0 flex-1">
                            <p className="text-xs font-medium text-muted-foreground">
                                {t(
                                    filter === "official" || detail.installed?.installation?.kind === "official"
                                        ? "settings.Official app"
                                        : "settings.Manual app"
                                )}
                            </p>
                            <h2 className="mt-1 break-words text-2xl font-semibold">{detail.name}</h2>
                            {detail.installed && (
                                <p className="mt-1 text-sm text-muted-foreground">
                                    {detail.installed.declaration.version} ·{" "}
                                    {t(detail.installed.is_enabled ? "settings.Installed app" : "settings.Disabled app")}
                                </p>
                            )}
                        </div>
                        {query.data.official.some((item) => item.key === detail.key) && installButton(detail.key, detail.installed)}
                    </header>
                    {errorBanner}
                    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
                        <div className="min-w-0 space-y-6">
                            <div className="rounded-2xl border bg-card p-5">
                                <h3 className="font-semibold">{t("settings.About this app")}</h3>
                                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{detail.description}</p>
                            </div>
                            {!!detail.installed?.installation?.store?.screenshots?.length && (
                                <div className="flex gap-4 overflow-x-auto pb-2">
                                    {detail.installed.installation.store.screenshots.map((asset) => (
                                        <img
                                            key={asset}
                                            src={packageAsset(detail.key, detail.installed?.installation, asset)}
                                            alt={t("settings.App preview")}
                                            loading="lazy"
                                            className="max-h-80 max-w-full rounded-2xl border object-contain"
                                        />
                                    ))}
                                </div>
                            )}
                        </div>
                        <aside className="space-y-4 rounded-2xl border bg-card p-5">
                            <h3 className="flex items-center gap-2 font-semibold">
                                <ShieldCheck className="size-4" />
                                {t("settings.App permissions")}
                            </h3>
                            <p className="text-xs leading-5 text-muted-foreground">{t("settings.Central app installation help")}</p>
                            <ul className="space-y-2 text-xs">
                                {detail.installed?.declaration.capabilities?.map((capability) => (
                                    <li className="break-all rounded-md bg-muted px-2 py-1" key={capability}>
                                        {capability}
                                    </li>
                                ))}
                            </ul>
                            {detail.repository && (
                                <a
                                    className="block break-all text-xs text-muted-foreground underline"
                                    href={`https://github.com/${detail.repository}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                >
                                    {detail.repository}
                                </a>
                            )}
                        </aside>
                    </div>
                </>
            ) : (
                <>
                    <header className="relative overflow-hidden rounded-3xl border bg-gradient-to-br from-primary/10 via-card to-card p-6 sm:p-8">
                        <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground">Langboard</p>
                        <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">{t("settings.App store")}</h2>
                        <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">{t("settings.Discover apps help")}</p>
                        <Button
                            className="mt-5 rounded-full"
                            variant="outline"
                            disabled={pending}
                            onClick={() => {
                                setFile(null);
                                setManualOpen((value) => !value);
                            }}
                        >
                            <Upload className="mr-2 size-4" />
                            {t("settings.Manual ZIP installation")}
                        </Button>
                    </header>
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                        <nav aria-label={t("settings.App catalog filter")} className="flex flex-wrap gap-1 rounded-xl bg-muted p-1">
                            {(["official", "installed", "manual"] as const).map((value) => (
                                <Button
                                    key={value}
                                    size="sm"
                                    variant={filter === value ? "outline" : "ghost"}
                                    aria-pressed={filter === value}
                                    onClick={() => setFilter(value)}
                                >
                                    {t(
                                        value === "official"
                                            ? "settings.Official catalog"
                                            : value === "installed"
                                              ? "settings.Installed apps"
                                              : "settings.Manual app"
                                    )}
                                </Button>
                            ))}
                        </nav>
                        <label className="relative w-full sm:w-64">
                            <Search className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" aria-hidden="true" />
                            <Input
                                className="pl-9"
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                placeholder={t("settings.Search apps")}
                                aria-label={t("settings.Search apps")}
                            />
                        </label>
                    </div>
                    {errorBanner}
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        {visible.map((item) => (
                            <article
                                key={item.key}
                                className={
                                    "card card-border flex min-w-0 flex-col gap-4 rounded-2xl border " +
                                    "bg-card p-5 shadow-sm transition-shadow hover:shadow-md"
                                }
                            >
                                <button
                                    className="flex min-w-0 items-center gap-4 text-left"
                                    onClick={() => setSelectedKey(item.key)}
                                    aria-label={`${t("settings.App details")}: ${item.name}`}
                                >
                                    <AppPackageIcon
                                        appKey={item.key}
                                        installation={item.installed?.installation}
                                        fallbackUrl={item.icon_url}
                                        fallbackDarkUrl={item.icon_dark_url}
                                    />
                                    <div className="min-w-0">
                                        <h3 className="break-words font-semibold">{item.name}</h3>
                                        <span className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                                            {(filter === "official" || item.installed?.installation?.kind === "official") && (
                                                <ShieldCheck className="size-3" />
                                            )}
                                            {t(
                                                filter === "official" || item.installed?.installation?.kind === "official"
                                                    ? "settings.Official app"
                                                    : "settings.Manual app"
                                            )}
                                        </span>
                                    </div>
                                </button>
                                <p className="line-clamp-3 flex-1 text-sm leading-6 text-muted-foreground">{item.description}</p>
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    {query.data.official.some((app) => app.key === item.key) ? (
                                        installButton(item.key, item.installed)
                                    ) : (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => {
                                                setUpdateKey(item.key);
                                                setManualOpen(true);
                                            }}
                                        >
                                            {t("settings.Update app")}
                                        </Button>
                                    )}
                                    {item.installed && (
                                        <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                            <Check className="size-3" />
                                            {item.installed.declaration.version}
                                        </span>
                                    )}
                                </div>
                            </article>
                        ))}
                    </div>
                    {!visible.length && (
                        <p className="rounded-2xl border p-8 text-center text-sm text-muted-foreground">{t("settings.No apps found")}</p>
                    )}
                    {manualOpen && (
                        <div className="rounded-2xl border bg-card p-5">
                            <h3 className="mb-4 flex items-center gap-2 font-semibold">
                                <Package className="size-4" />
                                {t("settings.Manual ZIP installation")}
                            </h3>
                            <label className="block text-sm" htmlFor="app-package-file">
                                {t("settings.App ZIP package")}
                            </label>
                            <input
                                id="app-package-file"
                                ref={fileInput}
                                className="mt-2 w-full min-w-0 text-sm"
                                type="file"
                                accept=".zip,application/zip"
                                aria-describedby="app-package-file-help"
                                aria-invalid={invalidFile}
                                disabled={pending || failed}
                                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                            />
                            <p id="app-package-file-help" className={`mt-2 text-xs ${invalidFile ? "text-destructive" : "text-muted-foreground"}`}>
                                {t("settings.App ZIP size help")}
                            </p>
                            <label className="mt-4 block text-sm" htmlFor="app-package-update">
                                {t("settings.Manual installation target")}
                            </label>
                            <select
                                id="app-package-update"
                                className="mt-2 w-full rounded-md border bg-background p-2 text-sm"
                                value={updateKey}
                                disabled={pending || failed}
                                onChange={(event) => setUpdateKey(event.target.value)}
                            >
                                <option value="">{t("settings.New app")}</option>
                                {installed.map((app) => (
                                    <option key={app.declaration.key} value={app.declaration.key}>
                                        {app.declaration.name} · {app.declaration.version}
                                    </option>
                                ))}
                            </select>
                            <Button
                                className="mt-4 rounded-full"
                                variant="outline"
                                disabled={!file || invalidFile || pending || failed}
                                onClick={() => void install()}
                            >
                                {t("settings.Install ZIP")}
                            </Button>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
