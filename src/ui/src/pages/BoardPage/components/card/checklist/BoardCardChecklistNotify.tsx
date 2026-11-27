import Toast from "@/components/base/Toast";
import MultiSelectAssignee, { IFormProps, TSaveHandler } from "@/components/MultiSelectAssignee";
import useNotifyCardChecklist from "@/controllers/api/card/checklist/useNotifyCardChecklist";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { User } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { cn } from "@/core/utils/ComponentUtils";
import { IBoardCardCheckRelatedContextParams } from "@/pages/BoardPage/components/card/checklist/types";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

function BoardCardChecklistNotify() {
    const { projectUID, card, currentUser, hasRoleAction } = useBoardCard();
    const { model: checklist, params } = ModelRegistry.ProjectChecklist.useContext<IBoardCardCheckRelatedContextParams>();
    const { isValidating, setIsValidating } = params;
    const { mutateAsync: notifyChecklistMutateAsync } = useNotifyCardChecklist({ interceptToast: true });
    const [t] = useTranslation();
    const canEdit = hasRoleAction(ProjectRole.EAction.CardUpdate);
    const projectMembers = card.useForeignFieldArray("project_members");
    const validProjectMembers = useMemo(() => projectMembers.filter((member) => member.isValidUser()), [projectMembers]);
    const groups = currentUser.useForeignFieldArray("user_groups");

    const notify = useCallback(
        (selectedItems: User.TModel[]) => {
            if (isValidating || !selectedItems.length) {
                return;
            }

            setIsValidating(true);

            const promise = notifyChecklistMutateAsync({
                project_uid: projectUID,
                card_uid: card.uid,
                checklist_uid: checklist.uid,
                user_uids: selectedItems.map((item) => item.uid),
            });

            Toast.Add.promise(promise, {
                loading: t("card.Notifying..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler({}, messageRef);

                    handle(error);
                    return messageRef.message;
                },
                success: () => {
                    return t("successes.Notified members successfully.");
                },
                finally: () => {
                    setIsValidating(false);
                },
            });
        },
        [isValidating, setIsValidating]
    );

    return (
        <MultiSelectAssignee.Popover
            popoverButtonProps={{
                size: "icon",
                className: "h-8 w-5 sm:size-8",
                variant: "ghost",
                title: t("card.Notify members"),
            }}
            popoverContentProps={{
                className: cn(
                    "max-w-[calc(100vw_-_theme(spacing.20))]",
                    "sm:max-w-[calc(theme(screens.sm)_-_theme(spacing.60))]",
                    "lg:max-w-[calc(theme(screens.md)_-_theme(spacing.60))]",
                    "min-w-[min(theme(spacing.20),100%)]"
                ),
                align: "start",
            }}
            userAvatarListProps={{
                maxVisible: 6,
                size: { initial: "sm", lg: "default" },
                spacing: "none",
                listAlign: "start",
                className: "space-x-1",
            }}
            placeholder={t("card.Select members...")}
            allSelectables={validProjectMembers.filter((member) => member.uid !== currentUser.uid)}
            originalAssignees={[]}
            tagContentProps={{
                scope: {
                    projectUID,
                    cardUID: card.uid,
                },
            }}
            createSearchKeywords={((item: User.TModel) => [item.email, item.firstname, item.lastname]) as IFormProps["createSearchKeywords"]}
            createLabel={((item: User.TModel) => `${item.firstname} ${item.lastname}`.trim()) as IFormProps["createLabel"]}
            addIcon="bell-plus"
            addIconSize="5"
            saveText={t("card.Notify")}
            save={notify as TSaveHandler}
            withUserGroups
            groups={groups}
            filterGroupUser={(item: User.TModel) => validProjectMembers.some((member) => member.uid === item.uid)}
            canEdit={canEdit}
        />
    );
}

export default BoardCardChecklistNotify;
