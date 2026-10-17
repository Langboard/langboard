import { useTranslation } from "react-i18next";
import IconComponent from "@/components/base/IconComponent";
import Tooltip from "@/components/base/Tooltip";
import DropdownMenu from "@/components/base/DropdownMenu";
import { useRef } from "react";
import { cn } from "@/core/utils/ComponentUtils";

export interface IActivityRailItem {
    icon: string;
    label: string;
    onClick: () => void;
    active?: boolean;
    badge?: React.ReactNode;
    hidden?: boolean;
    placement?: "bottom";
    mobile?: "primary" | "app" | "add";
}

export default function ActivityRail({
    items,
    contextExpanded = false,
    onMobileNavigate,
    mobileContextRef,
}: {
    items: IActivityRailItem[];
    contextExpanded?: boolean;
    onMobileNavigate?: (trigger: HTMLButtonElement) => void;
    mobileContextRef?: React.RefObject<HTMLElement | null>;
}) {
    const [t] = useTranslation();
    const moreTrigger = useRef<HTMLButtonElement>(null);
    const selectedFromMenu = useRef(false);
    const visible = items.filter((item) => !item.hidden);
    const primary = visible.filter((item) => item.mobile === "primary");
    const direct = (primary.length ? primary : visible.filter((item) => !item.placement && !item.mobile)).slice(0, 2);
    const add = visible.find((item) => item.mobile === "add");
    direct.push(...visible.filter((item) => item.mobile === "app").slice(0, add ? 1 : 2));
    const overflow = visible.filter((item) => !direct.includes(item) && item !== add);
    const activate = (item: IActivityRailItem, trigger: HTMLButtonElement) => {
        onMobileNavigate?.(trigger);
        item.onClick();
    };
    const mobileButton = (item: IActivityRailItem) => (
        <button
            key={item.label}
            type="button"
            aria-label={item.label}
            aria-current={item.active ? "page" : undefined}
            onClick={(event) => activate(item, event.currentTarget)}
            className={cn(
                "relative flex h-14 min-w-11 flex-1 flex-col items-center justify-center gap-1 rounded-md px-1 text-muted-foreground",
                item.active && "bg-muted text-primary"
            )}
        >
            <IconComponent icon={item.icon} size="5" />
            <span className="max-w-full truncate text-[10px]">{item.label}</span>
            {item.badge && (
                <span className="absolute right-1 top-0 rounded-full bg-primary px-1 text-[9px] text-primary-foreground">{item.badge}</span>
            )}
        </button>
    );
    if (!visible.length) return null;
    return (
        <nav
            aria-label={t("common.Workspace")}
            data-workbench-context=""
            data-context-expanded={contextExpanded}
            className={cn(
                "fixed inset-x-0 bottom-0 z-[110] border-t bg-background pb-[env(safe-area-inset-bottom)]",
                "md:static md:z-auto md:flex md:w-12 md:shrink-0 md:flex-col md:items-center md:gap-1",
                "md:overflow-hidden md:border-r md:border-t-0 md:py-2"
            )}
        >
            <div className="flex h-14 items-center gap-1 px-2 md:hidden">
                {direct.map(mobileButton)}
                {!!overflow.length && (
                    <DropdownMenu.Root
                        onOpenChange={(open) => {
                            if (open) selectedFromMenu.current = false;
                        }}
                    >
                        <DropdownMenu.Trigger asChild>
                            <button
                                ref={moreTrigger}
                                type="button"
                                aria-label={t("common.More")}
                                className={cn(
                                    "flex h-14 min-w-11 flex-1 flex-col items-center justify-center gap-1 rounded-md text-muted-foreground",
                                    overflow.some((item) => item.active) && "bg-muted text-primary"
                                )}
                            >
                                <IconComponent icon="ellipsis" size="5" />
                                <span className="text-[10px]">{t("common.More")}</span>
                            </button>
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Content
                            side="top"
                            align="end"
                            className="z-[130] max-h-[60dvh] max-w-[calc(100vw-1rem)] overflow-y-auto"
                            onCloseAutoFocus={(event) => {
                                if (selectedFromMenu.current && mobileContextRef?.current) {
                                    event.preventDefault();
                                    mobileContextRef.current.focus({ preventScroll: true });
                                }
                            }}
                        >
                            {overflow.map((item) => (
                                <DropdownMenu.Item
                                    key={item.label}
                                    aria-current={item.active ? "page" : undefined}
                                    className="min-h-11"
                                    onSelect={() => {
                                        selectedFromMenu.current = true;
                                        if (moreTrigger.current) activate(item, moreTrigger.current);
                                    }}
                                >
                                    <IconComponent icon={item.icon} size="5" />
                                    <span className="truncate">{item.label}</span>
                                    {item.badge}
                                </DropdownMenu.Item>
                            ))}
                        </DropdownMenu.Content>
                    </DropdownMenu.Root>
                )}
                {add && mobileButton(add)}
            </div>
            {([undefined, "bottom"] as const)
                .filter((placement) => items.some((item) => !item.hidden && item.placement === placement))
                .map((placement) => (
                    <div
                        key={placement ?? "main"}
                        className={
                            placement === "bottom"
                                ? "mt-auto hidden shrink-0 flex-col gap-1 border-t pt-2 md:flex"
                                : "hidden min-h-0 flex-col gap-1 overflow-y-auto md:flex"
                        }
                    >
                        {items
                            .filter((item) => !item.hidden && item.placement === placement)
                            .map((item) => (
                                <Tooltip.Root key={`${item.label}:${contextExpanded}`} open={contextExpanded ? false : undefined}>
                                    <Tooltip.Trigger asChild>
                                        <button
                                            type="button"
                                            aria-label={item.label}
                                            aria-current={item.active ? "page" : undefined}
                                            onClick={item.onClick}
                                            className={cn(
                                                "relative flex size-10 shrink-0 items-center justify-center rounded-md text-muted-foreground",
                                                "hover:bg-muted hover:text-foreground",
                                                item.active && "bg-muted text-primary before:absolute before:inset-y-2",
                                                item.active && "before:left-0 before:w-0.5",
                                                item.active && "before:rounded-full before:bg-primary"
                                            )}
                                        >
                                            <IconComponent icon={item.icon} size="5" />
                                            {item.badge ? (
                                                <span
                                                    className={cn(
                                                        "absolute right-0 top-0 rounded-full bg-primary px-1",
                                                        "text-[9px] text-primary-foreground"
                                                    )}
                                                >
                                                    {item.badge}
                                                </span>
                                            ) : null}
                                        </button>
                                    </Tooltip.Trigger>
                                    {!contextExpanded && (
                                        <Tooltip.Portal>
                                            <Tooltip.Content side="right">{item.label}</Tooltip.Content>
                                        </Tooltip.Portal>
                                    )}
                                </Tooltip.Root>
                            ))}
                    </div>
                ))}
        </nav>
    );
}
