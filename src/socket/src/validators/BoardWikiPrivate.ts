import Subscription from "@/core/server/Subscription";
import ProjectWiki from "@/models/ProjectWiki";
import { ESocketTopic } from "@langboard/core/enums";

Subscription.registerValidator(ESocketTopic.BoardWikiPrivate, async (context) => {
    return await ProjectWiki.canAccessCollaboration(context.client.user.id, context.topicId, context.client.user.is_admin);
});
