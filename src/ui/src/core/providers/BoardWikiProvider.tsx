import Toast from "@/components/base/Toast";
import useBoardWikiCreatedHandlers from "@/controllers/socket/wiki/useBoardWikiCreatedHandlers";
import useBoardWikiDeletedHandlers from "@/controllers/socket/wiki/useBoardWikiDeletedHandlers";
import useBoardWikiProjectUsersUpdatedHandlers from "@/controllers/socket/wiki/useBoardWikiProjectUsersUpdatedHandlers";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useSwitchSocketHandlers from "@/core/hooks/useSwitchSocketHandlers";
import { AuthUser, Project, ProjectWiki, User } from "@/core/models";
import { ISocketContext, useSocket } from "@/core/providers/SocketProvider";
import { ROUTES } from "@/core/routing/constants";
import { getEditorStore } from "@/core/stores/EditorStore";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ProjectRole } from "@/core/models/roles";

export type TBoardWikiMode = "reorder" | "delete" | "view";

export interface IBoardWikiContext {
    project: Project.TModel;
    socket: ISocketContext;
    wikis: ProjectWiki.TModel[];
    projectMembers: User.TModel[];
    currentUser: AuthUser.TModel;
    canAccessWiki: (shouldNavigate: bool, uid?: string) => bool;
    canEditWiki: (uid?: string) => bool;
    modeType: TBoardWikiMode;
    setModeType: React.Dispatch<React.SetStateAction<TBoardWikiMode>>;
    isWikiEditing: bool;
    setIsWikiEditing: React.Dispatch<React.SetStateAction<bool>>;
    wikiTabListId: string;
    changeTab: (uid: string) => void;
}

interface IBoardWikiProviderProps {
    project: Project.TModel;
    projectMembers: User.TModel[];
    currentUser: AuthUser.TModel;
    children: React.ReactNode;
}

const initialContext = {
    project: {} as Project.TModel,
    socket: {} as ISocketContext,
    wikis: [],
    projectMembers: [],
    currentUser: {} as AuthUser.TModel,
    canAccessWiki: () => false,
    canEditWiki: () => false,
    modeType: "view" as TBoardWikiMode,
    setModeType: () => {},
    isWikiEditing: false,
    setIsWikiEditing: () => {},
    wikiTabListId: "",
    changeTab: () => {},
};

const BoardWikiContext = createContext<IBoardWikiContext>(initialContext);

export const BoardWikiProvider = ({
    project,
    projectMembers: flatProjectMembers,
    currentUser,
    children,
}: IBoardWikiProviderProps): React.ReactNode => {
    const socket = useSocket();
    const navigate = usePageNavigateRef();
    const wikis = ProjectWiki.Model.useModels((model) => model.project_uid === project.uid, [project]);
    const { wikiUID } = useParams();
    const [projectMembers, setProjectMembers] = useState(flatProjectMembers);
    const [t] = useTranslation();
    const [modeType, setModeType] = useState<TBoardWikiMode>("view");
    const [isWikiEditing, setIsWikiEditing] = useState(false);
    const wikiTabListId = `board-wiki-tab-list-${project.uid}`;
    const currentUserRoleActions = project.useField("current_auth_role_actions");
    const isAdmin = currentUser.useField("is_admin");
    const { hasRoleAction } = useRoleActionFilter(currentUserRoleActions);
    const boardWikiCreatedHandlers = useMemo(
        () =>
            useBoardWikiCreatedHandlers({
                projectUID: project.uid,
            }),
        [project]
    );
    const boardWikiDeletedHandlers = useMemo(
        () =>
            useBoardWikiDeletedHandlers({
                projectUID: project.uid,
                callback: (data) => {
                    if (wikiUID === data.uid) {
                        navigate(ROUTES.BOARD.WIKI(project.uid));
                        getEditorStore().setCurrentEditor(null);
                    }
                },
            }),
        [wikiUID]
    );
    const projectUsersUpdatedHandlers = useMemo(
        () =>
            useBoardWikiProjectUsersUpdatedHandlers({
                projectUID: project.uid,
                callback: (data) => {
                    setProjectMembers(() => data.assigned_members);
                },
            }),
        [setProjectMembers]
    );
    const handlers = useMemo(
        () => [boardWikiCreatedHandlers, boardWikiDeletedHandlers, projectUsersUpdatedHandlers],
        [boardWikiCreatedHandlers, boardWikiDeletedHandlers, projectUsersUpdatedHandlers]
    );

    useSwitchSocketHandlers({
        socket,
        handlers,
        dependencies: handlers,
    });

    useEffect(() => {
        const unsubscribes: (() => void)[] = [];
        for (let i = 0; i < wikis.length; ++i) {
            const wiki = wikis[i];
            unsubscribes.push(wiki.subscribePrivateSocketHandlers(currentUser));
        }

        return () => {
            for (let i = 0; i < unsubscribes.length; ++i) {
                unsubscribes[i]();
            }
        };
    }, [currentUser, wikis]);

    useEffect(() => {
        setIsWikiEditing(false);
    }, [wikiUID]);

    const canAccessWiki = (shouldNavigate: bool, uid?: string) => {
        if (!uid) {
            return true;
        }

        const wiki = ProjectWiki.Model.getModel(uid);
        if (!wiki || wiki.project_uid !== project.uid || wiki.forbidden) {
            if (shouldNavigate) {
                Toast.Add.error(t("errors.requests.PE2005"));
                getEditorStore().setCurrentEditor(null);
                navigate(ROUTES.BOARD.WIKI(project.uid));
            }
            return false;
        }

        return true;
    };
    const canEditWiki = (uid?: string) => {
        if (!uid) {
            return false;
        }

        const wiki = ProjectWiki.Model.getModel(uid);
        if (!wiki || wiki.project_uid !== project.uid || wiki.forbidden) {
            return false;
        }

        return isAdmin || hasRoleAction(ProjectRole.EAction.Update);
    };
    const changeTab = (uid: string) => {
        if (uid === wikiUID) {
            return;
        }

        setIsWikiEditing(false);
        if (canAccessWiki(true, uid)) {
            if (!uid) {
                navigate(ROUTES.BOARD.WIKI(project.uid));
            } else {
                navigate(ROUTES.BOARD.WIKI_PAGE(project.uid, uid));
            }
            getEditorStore().setCurrentEditor(null);
        } else {
            navigate(ROUTES.BOARD.WIKI(project.uid));
            getEditorStore().setCurrentEditor(null);
        }
    };

    return (
        <BoardWikiContext.Provider
            value={{
                project,
                socket,
                wikis,
                projectMembers,
                currentUser,
                canAccessWiki,
                canEditWiki,
                modeType,
                setModeType,
                isWikiEditing,
                setIsWikiEditing,
                wikiTabListId,
                changeTab,
            }}
        >
            {children}
        </BoardWikiContext.Provider>
    );
};

export const useBoardWiki = () => {
    const context = useContext(BoardWikiContext);
    if (!context) {
        throw new Error("useBoardWiki must be used within a BoardWikiProvider");
    }
    return context;
};
