import Popover from "@/components/base/Popover";
import NotificationSetting from "@/components/NotificationSetting";
import UserAvatar from "@/components/UserAvatar";
import UserAvatarUserCreateAssignedCardAction from "@/components/UserAvatarDefaultList/actions/UserCreateAssignedCardAction";
import UserAvatarUserUnassignAction from "@/components/UserAvatarDefaultList/actions/UserUnassignAction";
import UserAvatarDefaultViewActivitiesAction from "@/components/UserAvatarDefaultList/actions/ViewActivitiesAction";
import useSearchFilters from "@/core/hooks/useSearchFilters";
import { Project, User } from "@/core/models";
import { BOARD_FILTER_KEYS, IFilterMap } from "@/core/providers/BoardProvider";
import { ROUTES } from "@/core/routing/constants";
import { useTranslation } from "react-i18next";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { IUserAvatarDefaultListContext, useUserAvatarDefaultList } from "@/components/UserAvatarDefaultList/Provider";
import { useUserAvatar } from "@/components/UserAvatar/Provider";
import { useMemo } from "react";
import { ProjectRole } from "@/core/models/roles";

interface IUserAvatarDefaultUserListProps {
    user: User.TModel;
}

const getNotificationForm = (scopeModels: IUserAvatarDefaultListContext["scopeModels"]) => {
    if (!scopeModels.project) {
        return null;
    }

    const form: { project_uid: string; project_column_uid?: string; card_uid?: string; wiki_uid?: string } = {
        project_uid: scopeModels.project.uid,
    };
    let type: "project" | "column" | "card" | "wiki" = "project";
    let specificUID = scopeModels.project.uid;

    if (scopeModels.column) {
        type = "column";
        form.project_column_uid = scopeModels.column.uid;
        specificUID = scopeModels.column.uid;
    }

    if (scopeModels.card) {
        type = "card";
        form.card_uid = scopeModels.card.uid;
        specificUID = scopeModels.card.uid;
    }

    if (scopeModels.wiki) {
        type = "wiki";
        form.wiki_uid = scopeModels.wiki.uid;
        specificUID = scopeModels.wiki.uid;
    }

    return { type, form, specificUID };
};

function UserAvatarDefaultUserList({ user }: IUserAvatarDefaultUserListProps): React.JSX.Element {
    const { getAvatarHoverCardAttrs } = useUserAvatar();
    const { scopeModels, currentUser, hasRoleAction, isAssignee, setIsAssignee } = useUserAvatarDefaultList();
    const [t] = useTranslation();
    const {
        filters,
        toString: filtersToString,
        unique: uniqueFilters,
        forceUpdate: forceUpdateFilters,
    } = useSearchFilters<IFilterMap>({ filterKeys: BOARD_FILTER_KEYS, searchKey: "filters" }, [user, scopeModels]);
    const navigate = usePageNavigateRef();
    const notificationForm = useMemo(() => getNotificationForm(scopeModels), [scopeModels]);

    return (
        <>
            {scopeModels.project && isAssignee && (hasRoleAction(ProjectRole.EAction.CardWrite) || currentUser?.is_admin) && (
                <>
                    <UserAvatarUserCreateAssignedCardAction user={user} project={scopeModels.project} />
                    <UserAvatar.ListSeparator />
                </>
            )}
            {scopeModels.project && (
                <>
                    <UserAvatar.ListItem
                        onClick={() => {
                            if (!filters.members) {
                                filters.members = [];
                            }

                            filters.members.push(user.email);

                            uniqueFilters();
                            const newFiltersString = filtersToString();
                            navigate({
                                pathname: ROUTES.BOARD.MAIN(scopeModels.project!.uid),
                                search: `?filters=${newFiltersString}`,
                            });
                            forceUpdateFilters();
                        }}
                    >
                        {t("common.avatarActions.View cards")}
                    </UserAvatar.ListItem>
                    <UserAvatar.ListSeparator />
                </>
            )}
            {scopeModels?.project && (
                <>
                    <UserAvatarDefaultViewActivitiesAction
                        scopeModels={scopeModels as Required<IUserAvatarDefaultListContext["scopeModels"]> & { project: Project.TModel }}
                        currentUser={currentUser}
                    />
                    <UserAvatar.ListSeparator />
                </>
            )}
            {notificationForm && currentUser && currentUser.uid === user.uid && hasRoleAction(ProjectRole.EAction.Read) && (
                <>
                    <Popover.Root modal={false}>
                        <Popover.Trigger asChild>
                            <UserAvatar.ListItem>{t("common.avatarActions.Set notifications")}</UserAvatar.ListItem>
                        </Popover.Trigger>
                        <Popover.Content className="z-[999999]" {...getAvatarHoverCardAttrs()}>
                            <NotificationSetting.SpecificScopedPopover
                                type={notificationForm.type as "project"}
                                currentUser={currentUser}
                                form={notificationForm.form}
                                specificUID={notificationForm.specificUID}
                                onlyFlex
                            />
                        </Popover.Content>
                    </Popover.Root>
                    <UserAvatar.ListSeparator />
                </>
            )}
            {scopeModels.project &&
                currentUser?.uid !== user.uid &&
                isAssignee &&
                (hasRoleAction(ProjectRole.EAction.Update) || currentUser?.is_admin) && (
                    <>
                        <UserAvatarUserUnassignAction project={scopeModels.project} setIsAssignee={setIsAssignee} />
                        <UserAvatar.ListSeparator />
                    </>
                )}
        </>
    );
}

export default UserAvatarDefaultUserList;
