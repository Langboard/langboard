import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Project } from "@/core/models";
import { cn } from "@/core/utils/ComponentUtils";

const ProjectUnreadIndicator = memo(({ project, className }: { project: Project.TModel; className?: string }) => {
    const [t] = useTranslation();
    const personalCount = project.useField("user_unread_card_count");
    const boardCount = project.useField("board_unread_card_count");
    const count = personalCount ?? boardCount;
    const hasUnread = project.useField("board_has_unread_change");
    // Retain the existing dot for responses from producers predating cardinality.
    const knownCount = typeof count === "number" && Number.isFinite(count);
    const unread = knownCount ? Math.max(0, Math.floor(count)) : hasUnread ? 1 : 0;
    const dotOnly = unread === 1 && personalCount == null;
    if (!unread) return null;
    const label = knownCount ? t("dashboard.Unread board cards", { count: unread }) : t("board.Unread changes");
    return (
        <span
            role="img"
            aria-label={label}
            title={label}
            data-board-unread-count={unread}
            className={cn(
                "pointer-events-none absolute z-10 rounded-full bg-primary text-primary-foreground",
                dotOnly ? "size-2" : "inline-flex h-4 min-w-4 items-center justify-center px-1 text-[10px] font-semibold leading-none",
                className
            )}
        >
            {dotOnly ? null : unread > 99 ? "99+" : unread}
        </span>
    );
});
ProjectUnreadIndicator.displayName = "Dashboard.ProjectUnreadIndicator";
export default ProjectUnreadIndicator;
