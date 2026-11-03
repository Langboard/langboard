import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import Command from "@/components/base/Command";
import Dialog from "@/components/base/Dialog";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import useBoardSplit from "@/core/hooks/useBoardSplit";
import { buildProjectQuickSwitcherSections } from "@/pages/DashboardPage/components/ProjectDiscovery";

export default function BoardSplitChooser({ currentProjectUID, onClose }: { currentProjectUID: string; onClose: () => void }) {
    const [t] = useTranslation();
    const split = useBoardSplit();
    const { data, isLoading, isError, isFetching, refetch } = useGetProjects();
    const sections = useMemo(
        () => buildProjectQuickSwitcherSections((data?.projects ?? []).filter((project) => project.uid !== currentProjectUID)),
        [currentProjectUID, data]
    );
    return (
        <Command.Dialog open onOpenChange={(open) => !open && onClose()}>
            <Dialog.Title className="sr-only">{t("dashboard.Search projects")}</Dialog.Title>
            <Dialog.Description className="sr-only">{t("dashboard.Projects")}</Dialog.Description>
            <Command.Input placeholder={t("dashboard.Search projects...")} aria-label={t("dashboard.Search projects")} />
            <Command.List>
                <Command.Empty>{isLoading ? t("common.Loading...") : t("dashboard.No projects found")}</Command.Empty>
                {isError && (
                    <div role="alert" className="p-3">
                        <span>{t("dashboard.Could not load projects")}</span>
                        <Button size="sm" variant="outline" disabled={isFetching} onClick={() => void refetch()}>
                            {t("dashboard.Retry")}
                        </Button>
                    </div>
                )}
                {(
                    [
                        ["favorites", "dashboard.Favorites"],
                        ["related", "dashboard.Related to me"],
                        ["recent", "dashboard.Recent work"],
                        ["other", "dashboard.Other projects"],
                    ] as const
                ).map(([key, heading]) =>
                    sections[key].length ? (
                        <Command.Group key={key} heading={t(heading)}>
                            {sections[key].map((project) => (
                                <Command.Item
                                    key={project.uid}
                                    value={project.uid}
                                    keywords={[project.title]}
                                    onSelect={() => {
                                        split?.open(project.uid);
                                        onClose();
                                    }}
                                    className="gap-3 rounded-lg py-2.5"
                                >
                                    <IconComponent icon={project.starred ? "star" : "folder-kanban"} size="4" />
                                    <span className="truncate">{project.title}</span>
                                </Command.Item>
                            ))}
                        </Command.Group>
                    ) : null
                )}
            </Command.List>
        </Command.Dialog>
    );
}
