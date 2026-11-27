import { cn } from "@/core/utils/ComponentUtils";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";

interface ISyncBlockerProps {
    actionLabel?: string;
    label: string;
    onAction?: () => void;
}

export default function SyncBlocker({ actionLabel, label, onAction }: ISyncBlockerProps) {
    return (
        <div
            className={cn(
                "absolute inset-0 z-[30] flex cursor-wait items-center justify-center gap-2 rounded-md",
                "border border-dashed border-primary/40 bg-background/75 px-2 text-xs font-medium text-muted-foreground backdrop-blur-[1px]"
            )}
        >
            <span>{label}</span>
            {onAction && (
                <Button
                    aria-label={actionLabel}
                    className="size-7 cursor-pointer p-0"
                    size="icon-sm"
                    title={actionLabel}
                    type="button"
                    variant="ghost"
                    onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onAction();
                    }}
                >
                    <IconComponent icon="refresh-ccw" size="4" />
                </Button>
            )}
        </div>
    );
}
