import type { TCardPriority } from "@langboard/core/constants";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import Popover from "@/components/base/Popover";
import CardPriorityField from "./CardPriorityField";

export default function CardPriorityPopover({
    value,
    disabled,
    save,
    onOpenChange,
    effectsEnabled = true,
}: {
    value: TCardPriority;
    disabled: boolean;
    save: (value: TCardPriority) => Promise<unknown>;
    onOpenChange?: (open: boolean) => void;
    effectsEnabled?: boolean;
}) {
    const [t] = useTranslation();
    return (
        <Popover.Root onOpenChange={onOpenChange}>
            <Popover.Trigger asChild>
                <Button type="button" variant="outline" size="sm" className="w-fit gap-1.5" aria-label={t("priority.label")}>
                    <IconComponent icon="flag" size="3.5" aria-hidden="true" />
                    {t("priority.label")} · {t(`priority.levels.${value}.label`)}
                </Button>
            </Popover.Trigger>
            <Popover.Content align="start" className="w-80 max-w-[calc(100vw-2rem)] border-0 p-0">
                <CardPriorityField value={value} disabled={disabled} save={save} effectsEnabled={effectsEnabled} />
            </Popover.Content>
        </Popover.Root>
    );
}
