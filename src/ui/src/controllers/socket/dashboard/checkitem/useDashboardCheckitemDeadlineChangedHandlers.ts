import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ProjectCheckitem } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IDashboardCheckitemDeadlineChangedRawResponse {
    uid: string;
    deadline_at?: Date | null;
}

export interface IUseDashboardCheckitemDeadlineChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
}

const useDashboardCheckitemDeadlineChangedHandlers = ({ callback, projectUID }: IUseDashboardCheckitemDeadlineChangedHandlersProps) => {
    return useSocketHandler<{}, IDashboardCheckitemDeadlineChangedRawResponse>({
        topic: ESocketTopic.Dashboard,
        topicId: projectUID,
        eventKey: `dashboard-checkitem-deadline-changed-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.DASHBOARD.CHECKITEM.DEADLINE_CHANGED,
            params: { uid: projectUID },
            callback,
            responseConverter: (data) => {
                const checkitem = ProjectCheckitem.Model.getModel(data.uid);
                if (checkitem) {
                    checkitem.deadline_at = data.deadline_at ? new Date(data.deadline_at) : undefined;
                }
                return {};
            },
        },
    });
};

export default useDashboardCheckitemDeadlineChangedHandlers;
