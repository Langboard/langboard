import { TBotScopeRelatedParams } from "@/controllers/api/shared/botScopes/types";
import { DomainEventType } from "@/core/events/DomainEventType";
import { TBotScopeModel, TBotScopeModelName } from "@/core/models/ModelRegistry";
import { BOT_SCOPES } from "@/core/constants/BotRelatedConstants";
import { createContext, useContext } from "react";

export interface IBotTriggerConditionListContext {
    params: TBotScopeRelatedParams;
    botUID: string;
    categories: Record<string, DomainEventType[]>;
    botScope?: TBotScopeModel<TBotScopeModelName>;
}

interface IBotTriggerConditionListProviderProps {
    params: TBotScopeRelatedParams;
    botUID: string;
    botScope?: TBotScopeModel<TBotScopeModelName>;
    children: React.ReactNode;
}

const initialContext = {
    params: {} as TBotScopeRelatedParams,
    botUID: "",
    categories: {},
    botScope: undefined,
};

const BotTriggerConditionListContext = createContext<IBotTriggerConditionListContext>(initialContext);

export const BotTriggerConditionListProvider = ({ params, botUID, botScope, children }: IBotTriggerConditionListProviderProps): React.ReactNode => {
    let categories;
    const targetModel = BOT_SCOPES[params.target_table];
    if (targetModel) {
        categories = targetModel.CATEGORIZED_EVENT_TYPES;
    } else {
        throw new Error(`Unsupported target_table: ${(params as Record<string, string>).target_table}`);
    }

    return (
        <BotTriggerConditionListContext.Provider
            value={{
                params,
                botUID,
                categories,
                botScope,
            }}
        >
            {children}
        </BotTriggerConditionListContext.Provider>
    );
};

export const useBotTriggerConditionList = () => {
    const context = useContext(BotTriggerConditionListContext);
    if (!context) {
        throw new Error("useBotTriggerConditionList must be used within an BotTriggerConditionListProvider");
    }
    return context;
};
