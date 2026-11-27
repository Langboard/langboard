import { createContext, memo, useContext, useEffect, useMemo, useState } from "react";
import { AuthUser, Project, ProjectCard, ProjectColumn, ProjectWiki } from "@/core/models";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAuth } from "@/core/providers/AuthProvider";
import useIsProjectAssignee from "@/controllers/api/board/useIsProjectAssignee";
import { ISocketContext, useSocket } from "@/core/providers/SocketProvider";
import { isModel, TUserLikeModel } from "@/core/models/ModelRegistry";
import { ProjectRole } from "@/core/models/roles";

export interface IUserAvatarDefaultListContext {
    socket: ISocketContext;
    userOrBot: TUserLikeModel;
    scopeModels: {
        project?: Project.TModel;
        column?: ProjectColumn.TModel;
        card?: ProjectCard.TModel;
        wiki?: ProjectWiki.TModel;
    };
    currentUser: AuthUser.TModel;
    hasRoleAction: ReturnType<typeof useRoleActionFilter<ProjectRole.TActions>>["hasRoleAction"];
    isAssignee: bool;
    setIsAssignee: React.Dispatch<React.SetStateAction<bool>>;
}

export interface IUserAvatarDefaultListProviderProps {
    scope?: {
        projectUID?: string;
        columnUID?: string;
        cardUID?: string;
        wikiUID?: string;
    };
    userOrBot: TUserLikeModel;
    children: React.ReactNode;
}

const initialContext = {
    socket: {} as ISocketContext,
    userOrBot: {} as TUserLikeModel,
    scopeModels: {},
    currentUser: {} as AuthUser.TModel,
    hasRoleAction: () => false,
    isAssignee: false,
    setIsAssignee: () => {},
};

const UserAvatarDefaultListContext = createContext<IUserAvatarDefaultListContext>(initialContext);

export const UserAvatarDefaultListProvider = memo(({ scope, userOrBot, children }: IUserAvatarDefaultListProviderProps): React.ReactNode => {
    const socket = useSocket();
    const scopeModels = useMemo(() => {
        if (!scope) {
            return {};
        }

        const models: IUserAvatarDefaultListContext["scopeModels"] = {};

        if (scope.projectUID) {
            models.project = Project.Model.getModel(scope.projectUID);
        }

        if (scope.columnUID) {
            models.column = ProjectColumn.Model.getModel(scope.columnUID);
        }

        if (scope.cardUID) {
            models.card = ProjectCard.Model.getModel(scope.cardUID);
        }

        if (scope.wikiUID) {
            models.wiki = ProjectWiki.Model.getModel(scope.wikiUID);
        }

        return models;
    }, [scope]);
    const project = useMemo(() => {
        if (scope?.projectUID) {
            return Project.Model.getModel(scope.projectUID);
        } else {
            return undefined;
        }
    }, [scope]);
    const { currentUser } = useAuth();
    const currentUserRoleActions = useMemo(() => {
        if (project) {
            return project.current_auth_role_actions;
        } else {
            return [];
        }
    }, [project]);
    const { hasRoleAction } = useRoleActionFilter(currentUserRoleActions);
    const { mutateAsync: isProjectAssigneeMutateAsync } = useIsProjectAssignee();
    const [isAssignee, setIsAssignee] = useState(false);
    const isValidUser = useMemo(() => {
        if (isModel(userOrBot, "User")) {
            return userOrBot.isValidUser();
        }
        return false;
    }, [userOrBot]);

    useEffect(() => {
        if (!project || !isValidUser) {
            setIsAssignee(() => false);
            return;
        }

        isProjectAssigneeMutateAsync(
            {
                project_uid: project.uid,
                assignee_uid: userOrBot.uid,
            },
            {
                onSuccess: (res) => {
                    setIsAssignee(() => res.result);
                },
            }
        );
    }, [userOrBot, isValidUser, project]);

    if (!currentUser) {
        return <></>;
    }

    return (
        <UserAvatarDefaultListContext.Provider
            value={{
                socket,
                userOrBot,
                scopeModels,
                currentUser,
                hasRoleAction,
                isAssignee,
                setIsAssignee,
            }}
        >
            {children}
        </UserAvatarDefaultListContext.Provider>
    );
});

export const useUserAvatarDefaultList = () => {
    const context = useContext(UserAvatarDefaultListContext);
    if (!context) {
        throw new Error("useUserAvatarDefaultList must be used within a UserAvatarDefaultListProvider");
    }
    return context;
};
