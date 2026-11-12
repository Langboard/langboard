import Flex from "@/components/base/Flex";
import NotificationSetting from "@/components/NotificationSetting";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { memo } from "react";
import { useTranslation } from "react-i18next";

const BoardCardNotificationSettings = memo(() => {
    const { projectUID, card, currentUser } = useBoardCard();
    const [t] = useTranslation();

    return (
        <Flex inline>
            <NotificationSetting.SpecificScopedPopover
                type="card"
                form={{
                    project_uid: projectUID,
                    card_uid: card.uid,
                }}
                currentUser={currentUser}
                specificUID={card.uid}
                triggerProps={{
                    variant: "ghost",
                    size: "icon-sm",
                    className: "size-8 text-primary hover:text-primary",
                    "aria-label": t("notification.settings.Notification settings"),
                    title: t("notification.settings.Notification settings"),
                }}
                iconProps={{
                    size: "4",
                    strokeWidth: "2",
                }}
                onlyPopover
            />
        </Flex>
    );
});

export default BoardCardNotificationSettings;
