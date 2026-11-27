import { AuthUser, BotModel, Project } from "@/core/models";
import { ISocketContext, useSocket } from "@/core/providers/SocketProvider";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ProjectRole } from "@/core/models/roles";
import { createContext, useContext, useMemo, useState } from "react";

export interface IBoardSettingsContext {
    socket: ISocketContext;
    project: Project.TModel;
    allBots: BotModel.TModel[];
    currentUser: AuthUser.TModel;
    canEditBasicInfo: bool;
    isBasicInfoEditing: bool;
    setIsBasicInfoEditing: React.Dispatch<React.SetStateAction<bool>>;
}

interface IBoardSettingsProviderProps {
    project: Project.TModel;
    currentUser: AuthUser.TModel;
    children: React.ReactNode;
}

const initialContext = {
    socket: {} as ISocketContext,
    project: {} as Project.TModel,
    allBots: [],
    currentUser: {} as AuthUser.TModel,
    canEditBasicInfo: false,
    isBasicInfoEditing: false,
    setIsBasicInfoEditing: () => {},
};

const BoardSettingsContext = createContext<IBoardSettingsContext>(initialContext);

export const BoardSettingsProvider = ({ project, currentUser, children }: IBoardSettingsProviderProps): React.ReactNode => {
    const socket = useSocket();
    const allBots = BotModel.Model.useModels(() => true);
    const [isBasicInfoEditing, setIsBasicInfoEditing] = useState(false);
    const currentUserRoleActions = project.useField("current_auth_role_actions");
    const isAdmin = currentUser.useField("is_admin");
    const { hasRoleAction } = useRoleActionFilter(currentUserRoleActions);
    const canEditBasicInfo = useMemo(() => isAdmin || hasRoleAction(ProjectRole.EAction.Update), [hasRoleAction, isAdmin]);

    return (
        <BoardSettingsContext.Provider
            value={{
                socket,
                project,
                allBots,
                currentUser,
                canEditBasicInfo,
                isBasicInfoEditing,
                setIsBasicInfoEditing,
            }}
        >
            {children}
        </BoardSettingsContext.Provider>
    );
};

export const useBoardSettings = () => {
    const context = useContext(BoardSettingsContext);
    if (!context) {
        throw new Error("useBoardSettings must be used within a BoardSettingsProvider");
    }
    return context;
};
