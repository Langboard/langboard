import { AuthUser, Project } from "@/core/models";
import { ISocketContext, useSocket } from "@/core/providers/SocketProvider";
import { ROUTES } from "@/core/routing/constants";
import { ESocketTopic } from "@langboard/core/enums";
import { createContext, useContext, useEffect } from "react";

export interface IDashboardContext {
    currentUser: AuthUser.TModel;
    socket: ISocketContext;
}

interface IDashboardProviderProps {
    currentUser: AuthUser.TModel;
    children: React.ReactNode;
}

const initialContext = {
    currentUser: {} as AuthUser.TModel,
    socket: {} as ISocketContext,
};

const DashboardContext = createContext<IDashboardContext>(initialContext);

export const DashboardProvider = ({ currentUser, children }: IDashboardProviderProps): React.ReactNode => {
    const socket = useSocket();
    const projects = Project.Model.useModels(() => true);

    useEffect(() => {
        const subscribableProjects: Project.TModel[] = [];
        for (let i = 0; i < projects.length; ++i) {
            const project = projects[i];
            if (socket.isSubscribed(ESocketTopic.Dashboard, project.uid)) {
                continue;
            }
            subscribableProjects.push(project);
        }

        if (!subscribableProjects.length) {
            return;
        }

        socket.subscribe(
            ESocketTopic.Dashboard,
            subscribableProjects.map((project) => project.uid)
        );

        return () => {
            if (location.pathname.startsWith(ROUTES.DASHBOARD.ROUTE)) {
                return;
            }

            socket.unsubscribe(
                ESocketTopic.Dashboard,
                subscribableProjects.map((project) => project.uid)
            );
        };
    }, [projects, socket]);

    return (
        <DashboardContext.Provider
            value={{
                socket,
                currentUser,
            }}
        >
            {children}
        </DashboardContext.Provider>
    );
};

export const useDashboard = () => {
    const context = useContext(DashboardContext);
    if (!context) {
        throw new Error("useDashboard must be used within a DashboardProvider");
    }
    return context;
};
