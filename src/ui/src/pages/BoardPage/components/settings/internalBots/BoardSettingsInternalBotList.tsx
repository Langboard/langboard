import Flex from "@/components/base/Flex";
import { InternalBotModel } from "@/core/models";
import BoardSettingsInternalBot from "@/pages/BoardPage/components/settings/internalBots/BoardSettingsInternalBot";
import BoardCompactorGrant from "./BoardCompactorGrant";
import { memo } from "react";

const BoardSettingsInternalBotList = memo(() => {
    return (
        <Flex direction="col" gap="2" py="4">
            {Object.values(InternalBotModel.EInternalBotType)
                .filter(InternalBotModel.isExecutableInternalBotType)
                .map((botType) => {
                    return <BoardSettingsInternalBot key={`board-settings-internal-bot-${botType}`} botType={botType} />;
                })}
            <BoardCompactorGrant />
        </Flex>
    );
});

export default BoardSettingsInternalBotList;
