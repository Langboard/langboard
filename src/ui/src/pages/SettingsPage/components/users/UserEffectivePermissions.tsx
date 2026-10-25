import Button from "@/components/base/Button";
import Select from "@/components/base/Select";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import useUserEffectivePermissions from "@/controllers/api/settings/users/useUserEffectivePermissions";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

export default function UserEffectivePermissions({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const selectID = useId();
    const [selectedUID, setSelectedUID] = useState("");
    const catalog = useGetProjects({ staleTime: 0, refetchOnMount: "always", interceptToast: true });
    const projects = !catalog.isFetching && !catalog.isError ? (catalog.data?.projects ?? []) : [];
    const projectUID = projects.some((project) => project.uid === selectedUID) ? selectedUID : "";
    const { data, loading, error, retry } = useUserEffectivePermissions(uid, projectUID);
    const label = t("settings.Effective board read permission");
    return (
        <section aria-label={label} aria-busy={catalog.isFetching || loading} className="space-y-2 border-t pt-4">
            <h3 className="font-semibold">{label}</h3>
            <p className="text-sm text-muted-foreground">{t("settings.Effective board read description")}</p>
            {catalog.isFetching && <p role="status">{t("common.Loading...")}</p>}
            {catalog.isError && (
                <div className="space-y-2">
                    <p role="alert">{t("dashboard.Could not load projects")}</p>
                    <Button variant="outline" size="sm" onClick={() => void catalog.refetch()}>
                        {t("common.Retry")}
                    </Button>
                </div>
            )}
            {!catalog.isFetching && !catalog.isError && !projects.length && <p>{t("settings.No readable boards")}</p>}
            {!!projects.length && (
                <>
                    <label htmlFor={selectID} className="text-sm font-medium">
                        {t("settings.Select readable board")}
                    </label>
                    <Select.Root value={projectUID} onValueChange={setSelectedUID}>
                        <Select.Trigger id={selectID}>
                            <Select.Value placeholder={t("settings.Select readable board")} />
                        </Select.Trigger>
                        <Select.Content>
                            {projects.map((project) => (
                                <Select.Item key={project.uid} value={project.uid}>
                                    {project.title || project.uid}
                                </Select.Item>
                            ))}
                        </Select.Content>
                    </Select.Root>
                    {!projectUID && <p className="text-sm">{t("settings.Select board to inspect")}</p>}
                </>
            )}
            {loading && <p role="status">{t("common.Loading...")}</p>}
            {error && (
                <p role="alert">
                    {t(
                        error === 403
                            ? "settings.Directory forbidden"
                            : error === 404
                              ? "settings.Permission resource unavailable"
                              : "settings.User details failed"
                    )}
                </p>
            )}
            {data && (
                <div aria-live="polite" className="space-y-2 text-sm">
                    <p className="font-medium">{t(data.allowed ? "settings.Board read allowed" : "settings.Board read denied")}</p>
                    {!data.allowed && <p>{t("settings.Board read denied reason")}</p>}
                    {!!data.sources.length && (
                        <ul className="list-inside list-disc">
                            {data.sources.map((source) => (
                                <li key={`${source.type}:${source.uid || ""}`}>{t(`settings.Permission source ${source.type}`)}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
            {projectUID && (
                <Button variant="outline" size="sm" disabled={loading} onClick={retry}>
                    {t(error ? "common.Retry" : "common.Refresh")}
                </Button>
            )}
        </section>
    );
}
