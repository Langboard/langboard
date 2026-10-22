import { TBotLogRelatedParams } from "@/controllers/api/shared/botLogs/types";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ProjectCard, ProjectColumn, BotLogModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { useEffect, useRef, useState } from "react";
import { TBotRelatedTargetTable } from "@/core/models/types/bot.related.type";

export type TUseGetBotLogsForm = TBotLogRelatedParams & {
    target_uid: string;
};

const useGetBotLogs = (botUID: string, params: TUseGetBotLogsForm, limit: number = 20, options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();
    const [isLastPage, setIsLastPage] = useState(true);
    const isFetchingRef = useRef(false);
    const lastCurrentDateRef = useRef(new Date());
    const pageRef = useRef(0);

    let url;
    switch (params.target_table) {
        case "project":
            url = Utils.String.format(Routing.API.BOT.LOG.GET_ALL_BY_PROJECT, {
                bot_uid: botUID,
                project_uid: params.target_uid,
            });
            break;
        case "project_column":
            url = Utils.String.format(Routing.API.BOT.LOG.GET_ALL_BY_COLUMN, {
                bot_uid: botUID,
                project_column_uid: params.target_uid,
            });
            break;
        case "card":
            url = Utils.String.format(Routing.API.BOT.LOG.GET_ALL_BY_CARD, {
                bot_uid: botUID,
                card_uid: params.target_uid,
            });
            break;
        default:
            throw new Error("Invalid target_table");
    }

    const getBotLogs = async () => {
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

            switch (params.target_table as TBotRelatedTargetTable) {
                case "project_column":
                    ProjectColumn.Model.fromOne(res.data.target);
                    break;
                case "card":
                    ProjectCard.Model.fromOne(res.data.target);
                    break;
            }

            BotLogModel.Model.fromArray(res.data.logs, true);

            setIsLastPage(res.data.logs.length < limit);

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
            void getBotLogs().catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            pageRef.current = 0;
        };
    }, []);

    const result = mutate(["get-bot-logs"], getBotLogs, {
        ...options,
        retry: 0,
    });

    return { ...result, isLastPage, isFetchingRef };
};

export default useGetBotLogs;
