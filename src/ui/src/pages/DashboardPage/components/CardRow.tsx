import Table from "@/components/base/Table";
import Button from "@/components/base/Button";
import DateDistance from "@/components/DateDistance";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCard } from "@/core/models";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import { add as addDate, differenceInSeconds, intervalToDuration } from "date-fns";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

interface ICardRowCellProps {
    card: ProjectCard.TModel;
    field: "title" | "column" | "created" | "duration";
}

/** Each cell subscribes only to its displayed fields and archive presentation. */
export function CardRowCell({ card, field }: ICardRowCellProps): React.JSX.Element {
    const archivedAt = card.useField("archived_at");
    return (
        <span className={cn("block", !!archivedAt && "text-base-content/60 line-through [&_button]:opacity-70")}>
            {field === "title" ? (
                <CardTitle card={card} />
            ) : field === "column" ? (
                <CardColumn card={card} />
            ) : field === "created" ? (
                <CardCreated card={card} />
            ) : (
                <CardDuration card={card} />
            )}
        </span>
    );
}

function CardLink({ card, children }: { card: ProjectCard.TModel; children: React.ReactNode }) {
    const navigate = usePageNavigateRef();
    return (
        <Button
            variant="link"
            className="btn btn-link h-auto max-w-full whitespace-normal break-words p-0 text-left"
            onClick={() => navigate(ROUTES.BOARD.CARD(card.project_uid, card.uid))}
        >
            {children}
        </Button>
    );
}

function CardTitle({ card }: { card: ProjectCard.TModel }) {
    const title = card.useField("title");
    const sourceType = card.useField("source_type");
    const linkedResource = card.useField("linked_resource");
    const [t] = useTranslation();
    const displayTitle =
        sourceType === "project_wiki" || sourceType === "project_card"
            ? linkedResource?.status === "available"
                ? linkedResource.title
                : linkedResource?.status === "forbidden"
                  ? t(sourceType === "project_wiki" ? "wiki.Restricted wiki" : "wiki.Source unavailable")
                  : t("wiki.Source unavailable")
            : title;
    return <CardLink card={card}>{displayTitle}</CardLink>;
}

function CardColumn({ card }: { card: ProjectCard.TModel }) {
    const columnName = card.useField("project_column_name");
    return <CardLink card={card}>{columnName}</CardLink>;
}

function CardCreated({ card }: { card: ProjectCard.TModel }) {
    const createdAt = card.useField("created_at");
    return <DateDistance date={createdAt} />;
}

function CardDuration({ card }: { card: ProjectCard.TModel }) {
    const archivedAt = card.useField("archived_at");
    const createdAt = card.useField("created_at");
    const duration = useMemo(() => {
        if (!archivedAt) return {};
        const now = new Date();
        return intervalToDuration({
            start: now,
            end: addDate(now, { seconds: differenceInSeconds(archivedAt.getTime(), createdAt.getTime()) }),
        });
    }, [archivedAt, createdAt]);
    return <>{archivedAt && Utils.String.formatTimerDuration(duration)}</>;
}

export interface ICardRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
    card: ProjectCard.TModel;
}

// Retain the existing row API used by the linked-card authorization fixture.
function CardRow({ card, ...props }: ICardRowProps): React.JSX.Element {
    return (
        <Table.FlexRow {...props}>
            {(["title", "column", "created", "duration"] as const).map((field) => (
                <Table.FlexCell key={field} className={field === "title" || field === "column" ? "w-1/3" : "w-1/6"}>
                    <CardRowCell card={card} field={field} />
                </Table.FlexCell>
            ))}
        </Table.FlexRow>
    );
}

export default CardRow;
