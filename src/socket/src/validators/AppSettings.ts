import { FULL_ADMIN_ACCESS_EMAILS } from "@/Constants";
import DB from "@/core/db/DB";
import Subscription from "@/core/server/Subscription";
import ApiKeyRole from "@/models/ApiKeyRole";
import McpRole from "@/models/McpRole";
import SettingRole, { ESettingCategory } from "@/models/SettingRole";
import { ESocketTopic, ESettingSocketTopicID } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";

Subscription.registerValidator(ESocketTopic.AppSettings, async (context) => {
    if (FULL_ADMIN_ACCESS_EMAILS.includes(context.client.user.email)) {
        return true;
    }

    const topicId = Utils.String.convertSafeEnum(ESettingSocketTopicID, context.topicId);
    switch (topicId) {
        case ESettingSocketTopicID.ApiKey:
            return await ApiKeyRole.isAnyGranted(context.client.user.id, DB);
        case ESettingSocketTopicID.McpToolGroup:
            return await McpRole.isAnyGranted(context.client.user.id, DB);
        default:
            if (!context.client.user.is_admin) {
                return false;
            }

            if (Object.entries(ESettingCategory).some(([key, value]) => key === (topicId as string) || value === (topicId as string))) {
                return await SettingRole.isCategoryGranted(context.client.user.id, topicId as unknown as ESettingCategory, DB);
            }
    }
    return false;
});
