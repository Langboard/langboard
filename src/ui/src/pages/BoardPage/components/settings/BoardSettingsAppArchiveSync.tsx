import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { api } from "@/core/helpers/Api";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

/** Explicit opt-in for provider effects. Inbox receipt and native archive remain independent. */
export default function BoardSettingsAppArchiveSync({ app, onRefresh }: { app: CatalogApp; onRefresh: () => Promise<{ isError: boolean }> }) {
    const [t] = useTranslation();
    const { project, canEditBasicInfo } = useBoardSettings();
    const [opened, setOpened] = useState(false);
    const [types, setTypes] = useState(app.binding?.archive_sync_types ?? []);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef(false);
    const policies = Object.entries(app.card_lifecycle ?? {}).filter(([, value]) => value.archive?.mode === "external");
    if (!app.binding || policies.length === 0) return null;
    const canEnable =
        app.is_available !== false &&
        app.binding.state === "enabled" &&
        app.capabilities.includes("events.receive") &&
        app.binding.granted_capabilities.includes("events.receive");
    const refresh = async () => {
        setPending(true);
        try {
            const result = await onRefresh();
            setFailed(result.isError);
            if (!result.isError) setOpened(false);
        } catch {
            setFailed(true);
        } finally {
            setPending(false);
        }
    };
    const save = async () => {
        if (!app.binding || !canEditBasicInfo || pending || failed || writing.current || (types.length > 0 && !canEnable)) return;
        writing.current = true;
        setPending(true);
        try {
            await api.put(
                `/board/${project.uid}/apps/${encodeURIComponent(app.key)}/archive-sync`,
                {
                    expected_revision: app.binding.revision,
                    card_types: types,
                },
                { env: { interceptToast: true } as never }
            );
            const result = await onRefresh();
            setFailed(result.isError);
            if (!result.isError) setOpened(false);
        } catch {
            setFailed(true);
        } finally {
            writing.current = false;
            setPending(false);
        }
    };
    return (
        <div className="space-y-2">
            <Button variant="outline" size="sm" disabled={pending} onClick={() => setOpened((value) => !value)}>
                {t("project.settings.Review archive synchronization", { defaultValue: "Review archive synchronization" })}
            </Button>
            {opened && (
                <div className="space-y-3 rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">
                        {t("project.settings.Archive synchronization help", {
                            defaultValue: "Native archive is recorded first. Provider actions run only for the card types you explicitly enable.",
                        })}
                    </p>
                    {policies.map(([kind, value]) => (
                        <Checkbox
                            key={kind}
                            label={kind}
                            description={
                                value.archive.description ??
                                t("project.settings.Provider archive action", { defaultValue: "External provider action after archive" })
                            }
                            checked={types.includes(kind)}
                            disabled={!canEditBasicInfo || pending || failed || (!canEnable && !types.includes(kind))}
                            onCheckedChange={(checked) =>
                                setTypes((current) => (checked === true ? [...current, kind] : current.filter((item) => item !== kind)))
                            }
                        />
                    ))}
                    {failed && (
                        <p role="alert" className="text-sm">
                            {t("project.settings.Refresh required", { defaultValue: "Refresh required before writing again." })}
                        </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                        <Button
                            size="sm"
                            disabled={!canEditBasicInfo || pending || failed || (types.length > 0 && !canEnable)}
                            onClick={() => void save()}
                        >
                            {t("common.Save", { defaultValue: "Save" })}
                        </Button>
                        <Button variant="outline" size="sm" disabled={!canEditBasicInfo || pending || failed} onClick={() => setTypes([])}>
                            {t("project.settings.Disable archive synchronization", { defaultValue: "Disable archive synchronization" })}
                        </Button>
                        <Button variant="ghost" size="sm" disabled={pending} onClick={() => void refresh()}>
                            {t("common.Refresh", { defaultValue: "Refresh" })}
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
