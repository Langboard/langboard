import { TBotScheduleRelatedParams } from "@/controllers/api/shared/botSchedules/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import { useEffect, useRef, useState } from "react";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";
import { BOT_SCHEDULES, BOT_TARGET_MODELS } from "@/core/constants/BotRelatedConstants";

export type TUseGetBotSchedulesForm = TBotScheduleRelatedParams & {
    target_uid: string;
};

const useGetBotSchedules = (botUID: string, params: TUseGetBotSchedulesForm, limit: number = 20, options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();
    const [isLastPage, setIsLastPage] = useState(true);
    const isFetchingRef = useRef(false);
    const lastCurrentDateRef = useRef(new Date());
    const pageRef = useRef(0);

    let url;
    switch (params.target_table) {
        case "project":
            url = Utils.String.format(Routing.API.BOT.SCHEDULE.GET_ALL_BY_PROJECT, {
                bot_uid: botUID,
                project_uid: params.target_uid,
            });
            break;
        case "project_column":
            url = Utils.String.format(Routing.API.BOT.SCHEDULE.GET_ALL_BY_COLUMN, {
                bot_uid: botUID,
                project_column_uid: params.target_uid,
            });
            break;
        case "card":
            url = Utils.String.format(Routing.API.BOT.SCHEDULE.GET_ALL_BY_CARD, {
                bot_uid: botUID,
                card_uid: params.target_uid,
            });
            break;
        default:
            throw new Error("Invalid target_table");
    }

    const getBotSchedules = async () => {
        if ((isLastPage && pageRef.current) || isFetchingRef.current) {
            return {};
        }

        isFetchingRef.current = true;
        try {
            const nextPage = pageRef.current + 1;

            const res = await api.get(url, {
                params: {
                    bot_uid: botUID,
                    refer_time: lastCurrentDateRef.current,
                    page: nextPage,
                    limit,
                },
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            });

            const targetModel = BOT_TARGET_MODELS[params.target_table as TBotRelatedTargetTable];
            const scheduleModel = BOT_SCHEDULES[params.target_table as TBotRelatedTargetTable];

            if (targetModel && scheduleModel) {
                targetModel.Model.fromOne(res.data.target);
                scheduleModel.Model.fromArray(res.data.schedules, true);
            }

            setIsLastPage(res.data.schedules.length < limit);

            pageRef.current = nextPage;
            return {};
        } finally {
            isFetchingRef.current = false;
        }
    };

    useEffect(() => {
        if (pageRef.current) {
            return;
        }

        lastCurrentDateRef.current = new Date();
        const initialLoad = setTimeout(() => {
            void getBotSchedules().catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            pageRef.current = 0;
        };
    }, []);

    const result = mutate(["get-bot-schedules"], getBotSchedules, {
        ...options,
        retry: 0,
    });

    return { ...result, isLastPage, isFetchingRef };
};

export default useGetBotSchedules;
