import Button from "@/components/base/Button";
import Table from "@/components/base/Table";
import DateDistance from "@/components/DateDistance";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCard, ProjectCheckitem } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import { useTimerDuration } from "@/core/hooks/useTimerDuration";
import { useTranslation } from "react-i18next";

export interface ITrackingRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
    checkitem: ProjectCheckitem.TModel;
}

function TrackingRow({ checkitem, ...props }: ITrackingRowProps): React.JSX.Element | null {
    const cardUID = checkitem.useField("card_uid");
    const card = ProjectCard.Model.useModel(cardUID, [cardUID]);

    return card ? <TrackingRowWithCard {...props} checkitem={checkitem} card={card} /> : null;
}

function TrackingRowWithCard({ checkitem, card, className, ...props }: ITrackingRowProps & { card: ProjectCard.TModel }): React.JSX.Element {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const title = checkitem.useField("title");
    const rawStatus = checkitem.useField("status");
    const startedAt = checkitem.useField("initial_timer_started_at");
    const isChecked = checkitem.useField("is_checked");
    let status;
    switch (rawStatus) {
        case ProjectCheckitem.ECheckitemStatus.Started:
            status = t("dashboard.Started");
            break;
        case ProjectCheckitem.ECheckitemStatus.Paused:
            status = t("dashboard.Paused");
            break;
        default:
            status = t("dashboard.Stopped");
            break;
    }

    return (
        <Table.FlexRow
            {...props}
            className={cn(
                isChecked &&
                    cn(
                        "text-muted-foreground [&_button]:text-primary/70",
                        "after:absolute after:left-0 after:top-1/2 after:z-50 after:-translate-y-1/2",
                        "after:pointer-events-none after:h-px after:w-full after:bg-border"
                    ),
                className
            )}
        >
            <ModelRegistry.ProjectCheckitem.Provider model={checkitem}>
                <Table.FlexCell className="w-1/4 text-center">
                    <Button
                        variant="link"
                        className="size-auto max-w-full whitespace-normal p-0 [overflow-wrap:anywhere]"
                        onClick={() => navigate(ROUTES.BOARD.CARD(card.project_uid, card.uid))}
                    >
                        {title}
                    </Button>
                </Table.FlexCell>
                <Table.FlexCell className="w-1/4 text-center">
                    <TrackingRowCardTitle card={card} />
                </Table.FlexCell>
                <Table.FlexCell className="w-1/6 text-center">{status}</Table.FlexCell>
                <Table.FlexCell className="w-1/6 text-center">{startedAt && <DateDistance date={startedAt} />}</Table.FlexCell>
                <Table.FlexCell className="w-1/6 text-center">
                    <TrackingRowTimeTaken />
                </Table.FlexCell>
            </ModelRegistry.ProjectCheckitem.Provider>
        </Table.FlexRow>
    );
}

function TrackingRowCardTitle({ card }: { card: ProjectCard.TModel }) {
    const navigate = usePageNavigateRef();
    const title = card.useField("title");

    return (
        <Button
            variant="link"
            className="size-auto max-w-full whitespace-normal p-0 [overflow-wrap:anywhere]"
            onClick={() => navigate(ROUTES.BOARD.CARD(card.project_uid, card.uid))}
        >
            {title}
        </Button>
    );
}

export function TrackingRowTimeTaken() {
    useTranslation();
    const { model: checkitem } = ModelRegistry.ProjectCheckitem.useContext();
    const status = checkitem.useField("status");
    const accumulatedSeconds = checkitem.useField("accumulated_seconds");
    const timerStartedAt = checkitem.useField("timer_started_at");
    const duration = useTimerDuration(status === ProjectCheckitem.ECheckitemStatus.Started, accumulatedSeconds, timerStartedAt);

    return <>{Utils.String.formatTimerDuration(duration)}</>;
}

export default TrackingRow;
