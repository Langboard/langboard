import { cn } from "@/core/utils/ComponentUtils";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/base/IconComponent";
import { formatNumber } from "@/core/utils/LocaleFormat";

export const EXPLORER_SECTION_LIMIT = 10;

export interface IExplorerSectionProps {
    title: string;
    count: number;
    collapsed: boolean;
    expanded: boolean;
    onToggle: () => void;
    onExpand: () => void;
    headerProps?: React.ButtonHTMLAttributes<HTMLButtonElement>;
    children: React.ReactNode;
    actions?: React.ReactNode;
}

export default function ExplorerSection({
    title,
    count,
    collapsed,
    expanded,
    onToggle,
    onExpand,
    headerProps,
    children,
    actions,
}: IExplorerSectionProps) {
    const [t, i18n] = useTranslation();
    const contentID = useId();
    return (
        <section className="relative mb-3 shrink-0" aria-label={title}>
            <h2 className={actions ? "pr-7" : undefined}>
                <button
                    {...headerProps}
                    type="button"
                    aria-expanded={!collapsed}
                    aria-controls={contentID}
                    className={cn(
                        "flex min-w-0 flex-1 items-center gap-1 rounded px-2 py-1",
                        "text-left text-xs font-medium text-muted-foreground hover:bg-muted"
                    )}
                    onClick={onToggle}
                >
                    <IconComponent icon={collapsed ? "chevron-right" : "chevron-down"} size="3" />
                    <span className="min-w-0 flex-1 truncate">{title}</span>
                    <span>{formatNumber(count, i18n.language)}</span>
                    {headerProps?.draggable && <IconComponent icon="grip-vertical" size="3" />}
                </button>
            </h2>
            {actions && <div className="absolute right-0 top-0">{actions}</div>}
            <div id={contentID} hidden={collapsed}>
                {!collapsed && children}
                {!collapsed && count > EXPLORER_SECTION_LIMIT && (
                    <button
                        type="button"
                        aria-expanded={expanded}
                        className="rounded px-2 py-1 text-left text-xs text-muted-foreground hover:bg-muted"
                        onClick={onExpand}
                    >
                        {expanded ? t("dashboard.Show fewer items") : t("dashboard.Show more items", { count: count - EXPLORER_SECTION_LIMIT })}
                    </button>
                )}
            </div>
        </section>
    );
}
