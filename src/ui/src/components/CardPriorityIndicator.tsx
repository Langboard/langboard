import type { TCardPriority } from "@langboard/core/constants";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/base/IconComponent";
import { getPriorityChoice } from "@/core/constants/PriorityChoiceConfig";

/** Shared static urgency signal; text and shape preserve meaning without color or motion. */
export default function CardPriorityIndicator({ priority }: { priority: TCardPriority }) {
    const [t] = useTranslation();
    const label = t(`priority.levels.${priority}.label`);
    return (
        <span
            role="img"
            className="card-priority-label inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground"
            data-priority={priority}
            aria-label={`${t("priority.label")}: ${label}`}
            title={t(`priority.levels.${priority}.hint`)}
        >
            <IconComponent icon={getPriorityChoice(priority).icon} size="3.5" aria-hidden="true" />
            <span>{label}</span>
        </span>
    );
}
