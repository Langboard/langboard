import { formatDateTime } from "@/core/utils/LocaleFormat";
import CardMetadataPreview from "@/pages/BoardPage/components/card/CardMetadataPreview";
import { ProjectCard } from "@/core/models";
import Tooltip from "@/components/base/Tooltip";
import { useTranslation } from "react-i18next";
import useUpdateDateDistance from "@/core/hooks/useUpdateDateDistance";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";

export default function CardTimestamps({ card, compact = false }: { card: ProjectCard.TModel; compact?: boolean }) {
    const createdAt = card.useField("created_at");
    const updatedAt = card.useField("updated_at");
    const [t, i18n] = useTranslation();
    const createdDistance = useUpdateDateDistance(createdAt);
    const updatedDistance = useUpdateDateDistance(updatedAt);
    const exact = (date: Date) => formatDateTime(date, i18n.language, { timeStyle: "medium" });

    if (compact) {
        return (
            <span className="flex basis-full text-[11px] text-muted-foreground/80">
                <CardMetadataPreview card={card}>
                    <IconComponent icon="pencil-line" size="3" className="shrink-0 opacity-70" aria-hidden="true" />
                    <time dateTime={updatedAt.toISOString()}>{updatedDistance}</time>
                </CardMetadataPreview>
            </span>
        );
    }

    const createdLabel = t("card.Created at {{date}}", { date: exact(createdAt) });
    const updatedLabel = t("card.Updated at {{date}}", { date: exact(updatedAt) });
    return (
        <span className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground/80">
            <Tooltip.Root>
                <Tooltip.Trigger asChild>
                    <time
                        dateTime={createdAt.toISOString()}
                        tabIndex={0}
                        aria-label={createdLabel}
                        className={cn(
                            "inline-flex items-center gap-1 rounded-sm focus-visible:outline-none",
                            "focus-visible:ring-2 focus-visible:ring-ring"
                        )}
                    >
                        <IconComponent icon="calendar-plus" size="3" className="shrink-0 opacity-70" aria-hidden="true" />
                        {createdDistance}
                    </time>
                </Tooltip.Trigger>
                <Tooltip.Content>{createdLabel}</Tooltip.Content>
            </Tooltip.Root>
            <Tooltip.Root>
                <Tooltip.Trigger asChild>
                    <time
                        dateTime={updatedAt.toISOString()}
                        tabIndex={0}
                        aria-label={updatedLabel}
                        className="inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                        <IconComponent icon="pencil-line" size="3" className="shrink-0 opacity-70" aria-hidden="true" />
                        {updatedDistance}
                    </time>
                </Tooltip.Trigger>
                <Tooltip.Content>{updatedLabel}</Tooltip.Content>
            </Tooltip.Root>
        </span>
    );
}
