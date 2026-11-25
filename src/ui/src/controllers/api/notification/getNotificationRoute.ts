import { QUERY_NAMES } from "@/constants";
import { UserNotification } from "@/core/models";
import { ENotificationType } from "@/core/models/types/notification.type";
import { ROUTES } from "@/core/routing/constants";

export function getNotificationRoute({ type, records }: Pick<UserNotification.Interface, "type" | "records">): string | undefined {
    const projectUID = records.project?.uid;
    const cardUID = records.card?.uid;
    switch (type) {
        case ENotificationType.ProjectInvited: {
            const token = records.project_invitation?.encrypted_token;
            return typeof token === "string"
                ? `${ROUTES.BOARD.INVITATION}?${new URLSearchParams({ [QUERY_NAMES.PROJCT_INVITATION_TOKEN]: token })}`
                : undefined;
        }
        case ENotificationType.MentionedInWiki: {
            const wikiUID = records.project_wiki?.uid;
            return projectUID && wikiUID ? ROUTES.BOARD.WIKI_PAGE(projectUID, wikiUID) : undefined;
        }
        case ENotificationType.ScheduledRule:
            if (projectUID && !cardUID) return ROUTES.BOARD.MAIN(projectUID);
    }
    return projectUID && cardUID ? ROUTES.BOARD.CARD(projectUID, cardUID) : undefined;
}
