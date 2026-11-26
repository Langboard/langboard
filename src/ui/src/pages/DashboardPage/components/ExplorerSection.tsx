import { cn } from "@/core/utils/ComponentUtils";
import { useId, useLayoutEffect, useRef } from "react";
import { useDrag, useDragLayer, useDrop } from "react-dnd";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/base/IconComponent";
import { formatNumber } from "@/core/utils/LocaleFormat";

export const EXPLORER_SECTION_LIMIT = 10;

export interface IExplorerSectionProps {
    title: string;
    icon?: React.ComponentProps<typeof IconComponent>["icon"];
    className?: string;
    count: number;
    collapsed: boolean;
    expanded: boolean;
    onToggle: () => void;
    onExpand: () => void;
    headerProps?: React.ButtonHTMLAttributes<HTMLButtonElement>;
    children: React.ReactNode;
    actions?: React.ReactNode;
    reorder?: { id: string; index: number; preview: (from: string, to: string) => void; finish: (commit: boolean) => void };
}

export default function ExplorerSection({
    title,
    icon,
    className,
    count,
    collapsed,
    expanded,
    onToggle,
    onExpand,
    headerProps,
    children,
    actions,
    reorder,
}: IExplorerSectionProps) {
    const [t, i18n] = useTranslation();
    const contentID = useId();
    const sectionRef = useRef<HTMLElement | null>(null);
    const [{ isDragging }, drag, preview] = useDrag(
        () => ({
            type: "langboard-explorer-section",
            item: { id: reorder?.id, index: reorder?.index },
            canDrag: !!reorder && !!headerProps?.draggable,
            end: (_item, monitor) => reorder?.finish(monitor.didDrop()),
            collect: (monitor) => ({ isDragging: monitor.isDragging() }),
        }),
        [reorder, headerProps?.draggable]
    );
    const pointer = useDragLayer((monitor) => monitor.getClientOffset());
    const [{ isOver, item }, drop] = useDrop(
        () => ({
            accept: "langboard-explorer-section",
            canDrop: () => !!reorder && !!headerProps?.draggable,
            collect: (monitor) => ({
                isOver: monitor.isOver({ shallow: true }),
                item: monitor.getItem() as { id?: string; index?: number } | null,
            }),
            drop: () => ({}),
        }),
        [reorder, headerProps?.draggable]
    );
    useLayoutEffect(() => {
        if (!isOver || !reorder || !item?.id || typeof item.index !== "number" || item.id === reorder.id || !pointer) return;
        const bounds = sectionRef.current?.getBoundingClientRect();
        if (!bounds || pointer.y < bounds.top || pointer.y > bounds.bottom) return;
        const halfway = bounds.top + bounds.height / 2;
        if (item.index < reorder.index && pointer.y < halfway) return;
        if (item.index > reorder.index && pointer.y > halfway) return;
        item.index = reorder.index;
        reorder.preview(item.id, reorder.id);
    }, [isOver, item, pointer, reorder]);
    return (
        <section
            ref={(node) => {
                sectionRef.current = node;
                drop(node);
                preview(node);
            }}
            className={cn(
                "relative mb-3 shrink-0",
                className,
                isDragging && "rounded-md bg-muted/50 outline outline-dashed outline-1 outline-primary [&>*]:opacity-30"
            )}
            data-explorer-dragging={isDragging || undefined}
            aria-label={title}
        >
            <h2 className={actions ? "pr-7" : undefined}>
                <button
                    {...headerProps}
                    ref={(node) => {
                        drag(node);
                    }}
                    type="button"
                    aria-expanded={!collapsed}
                    aria-controls={contentID}
                    className={cn(
                        "flex min-w-0 flex-1 items-center gap-1 rounded px-2 py-1",
                        "text-left text-xs font-medium text-muted-foreground hover:bg-muted",
                        headerProps?.draggable && "cursor-grab active:cursor-grabbing"
                    )}
                    onClick={onToggle}
                >
                    <IconComponent icon={collapsed ? "chevron-right" : "chevron-down"} size="3" />
                    {icon && <IconComponent icon={icon} size="3" className="shrink-0" aria-hidden="true" />}
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
