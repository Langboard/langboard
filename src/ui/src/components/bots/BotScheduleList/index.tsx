import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Loading from "@/components/base/Loading";
import ScrollArea from "@/components/base/ScrollArea";
import InfiniteScroller from "@/components/InfiniteScroller";
import { BotModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { useCallback, useRef } from "react";
import BotScheduleListItemAddButton from "@/components/bots/BotScheduleList/ItemAddButton";
import BotScheduleListItem from "@/components/bots/BotScheduleList/Item";
import useGetBotSchedules from "@/controllers/api/shared/botSchedules/useGetBotSchedules";
import { TBotScheduleRelatedParams } from "@/controllers/api/shared/botSchedules/types";
import { BotScheduleListProvider } from "@/components/bots/BotScheduleList/Provider";
import { TPickedModelClass, TBotScheduleModelName, TBotScheduleModel } from "@/core/models/ModelRegistry";
import { TBotRelatedTargetModel } from "@/core/models/types/bot.related.type";
import { BOT_SCHEDULES, BOT_TARGET_MODEL_FOREIGN_KEY } from "@/core/constants/BotRelatedConstants";

export interface IBotScheduleListProps {
    bot: BotModel.TModel;
    params: TBotScheduleRelatedParams;
    target: TBotRelatedTargetModel;
}

function BotScheduleList({ params, ...props }: IBotScheduleListProps) {
    const scheduleModel = BOT_SCHEDULES[params.target_table];
    const filterModel = (model: TBotScheduleModel<TBotScheduleModelName>) =>
        model[BOT_TARGET_MODEL_FOREIGN_KEY[params.target_table] as never] === props.target.uid;

    if (!scheduleModel) {
        return <></>;
    }

    return (
        <BotScheduleListDisplay
            {...props}
            params={params}
            ScheduleModel={scheduleModel.Model}
            filterModel={filterModel as (model: TBotScheduleModel<TBotScheduleModelName>) => bool}
        />
    );
}

interface IBotScheduleListDisplayProps extends IBotScheduleListProps {
    ScheduleModel: TPickedModelClass<TBotScheduleModelName>;
    filterModel: (model: TBotScheduleModel<TBotScheduleModelName>) => bool;
}

function BotScheduleListDisplay({ bot, params, target, ScheduleModel, filterModel }: IBotScheduleListDisplayProps): React.JSX.Element {
    const { mutateAsync, isLastPage, isFetchingRef } = useGetBotSchedules(bot.uid, { ...params, target_uid: target.uid });
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const schedules = ScheduleModel.useModels((model) => model.bot_uid === bot.uid && filterModel(model));
    const nextPage = useCallback(async () => {
        if (isFetchingRef.current || isLastPage) {
            return false;
        }

        await mutateAsync({});
        return true;
    }, [isLastPage, mutateAsync]);

    return (
        <BotScheduleListProvider bot={bot} params={params} target={target}>
            <Flex direction="col" gap="2">
                <ScrollArea.Root viewportRef={viewportRef} mutable={schedules}>
                    <InfiniteScroller.NoVirtual
                        scrollable={() => viewportRef.current}
                        loadMore={nextPage}
                        hasMore={!isLastPage}
                        loader={
                            <Flex justify="center" mt="6" key={Utils.String.Token.shortUUID()}>
                                <Loading size="3" variant="secondary" />
                            </Flex>
                        }
                        className="max-h-[calc(70vh_-_theme(spacing.20)_-_theme(spacing.1))]"
                    >
                        <Box display={{ initial: "flex", sm: "grid" }} direction="col" gap="2" className="sm:grid-cols-2">
                            {schedules.map((schedule) => (
                                <BotScheduleListItem key={schedule.uid} schedule={schedule} />
                            ))}
                        </Box>
                    </InfiniteScroller.NoVirtual>
                </ScrollArea.Root>
                <BotScheduleListItemAddButton />
            </Flex>
        </BotScheduleListProvider>
    );
}

export default BotScheduleList;
