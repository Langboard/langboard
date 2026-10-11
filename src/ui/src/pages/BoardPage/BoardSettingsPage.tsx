import { memo, useEffect } from "react";
import { ROUTES } from "@/core/routing/constants";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { IBoardRelatedPageProps } from "@/pages/BoardPage/types";
import useGetProjectDetails from "@/controllers/api/board/useGetProjectDetails";
import BoardSettingsList from "@/pages/BoardPage/components/settings/BoardSettingsList";
import { BoardSettingsProvider } from "@/core/providers/BoardSettingsProvider";
import BoardSettingsUserList from "@/pages/BoardPage/components/settings/BoardSettingsUserList";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import SettingsLoadState from "@/pages/SettingsPage/SettingsLoadState";
import { EHttpStatus } from "@langboard/core/enums";

const BoardSettingsPage = memo(({ project, currentUser }: IBoardRelatedPageProps) => {
    const { data, error, isFetching, refetch } = useGetProjectDetails({ uid: project.uid, includeCards: false });
    const navigate = usePageNavigateRef();

    useEffect(() => {
        if (!error) {
            return;
        }

        const { handle } = setupApiErrorHandler({
            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                toast: false,
            },
            [EHttpStatus.HTTP_404_NOT_FOUND]: {
                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { replace: true }),
            },
        });

        handle(error);
    }, [error]);

    return (
        <>
            {!data && <SettingsLoadState error={Boolean(error)} isFetching={isFetching} retry={() => void refetch()} />}
            {data && (
                <BoardSettingsProvider project={project} currentUser={currentUser}>
                    <BoardSettingsList notifications={<BoardSettingsUserList currentUser={currentUser} project={project} />} />
                </BoardSettingsProvider>
            )}
        </>
    );
});

export default BoardSettingsPage;
