import Tooltip from "@/components/base/Tooltip";
import { formatDateTime } from "@/core/utils/LocaleFormat";
import { useTranslation } from "react-i18next";

export interface ColumnEvent {
    uid: string;
    activity_type: "card_created" | "card_moved";
    created_at: string;
    from_column?: { name?: string } | null;
    column?: { name?: string } | null;
    recorder?: { type: string; firstname?: string; lastname?: string; name?: string } | null;
}

/** The same persisted event supports the compact overview and the comment timeline. */
export default function CardColumnEvent({ event, showTransition = false }: { event: ColumnEvent; showTransition?: bool }) {
    const [t, i18n] = useTranslation();
    const date = new Date(event.created_at);
    if (!event.column?.name || !Number.isFinite(date.getTime())) return null;
    const exact = formatDateTime(date, i18n.language, { timeStyle: "medium" });
    const actor =
        (event.recorder?.type === "bot" ? event.recorder.name : [event.recorder?.firstname, event.recorder?.lastname].filter(Boolean).join(" ")) ||
        t("common.Unknown User");
    // Old records without a source snapshot must not infer it from adjacent events.
    const transition =
        event.activity_type === "card_moved" && event.from_column?.name ? `${event.from_column.name} › ${event.column.name}` : event.column.name;
    const detail = `${transition} · ${exact} · ${actor}`;
    return (
        <Tooltip.Root>
            <Tooltip.Trigger asChild>
                <time
                    data-card-activity-uid={event.uid}
                    dateTime={date.toISOString()}
                    tabIndex={0}
                    aria-label={detail}
                    className="max-w-full truncate rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                    {showTransition ? `${actor} · ${transition}` : event.column.name}
                </time>
            </Tooltip.Trigger>
            <Tooltip.Content>{detail}</Tooltip.Content>
        </Tooltip.Root>
    );
}
