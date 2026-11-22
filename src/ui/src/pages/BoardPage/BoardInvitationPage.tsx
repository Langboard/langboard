import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import { FormOnlyLayout } from "@/components/Layout";
import { QUERY_NAMES } from "@/constants";
import useAcceptProjectInvitation from "@/controllers/api/board/useAcceptProjectInvitation";
import useDeclineProjectInvitation from "@/controllers/api/board/useDeclineProjectInvitation";
import useGetProjectInvitation from "@/controllers/api/board/useGetProjectInvitation";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useLocation } from "react-router";

function BoardInvitationPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const location = useLocation();
    const navigate = usePageNavigateRef();
    const searchParams = new URLSearchParams(location.search);
    const token = searchParams.get(QUERY_NAMES.PROJCT_INVITATION_TOKEN);
    const [isValidating, setIsValidating] = useState(false);
    const [projectTitle, setProjectTitle] = useState("");
    const { mutate: getProjectInvitationMutate } = useGetProjectInvitation();
    const { mutate: acceptProjectInvitation } = useAcceptProjectInvitation();
    const { mutate: declineProjectInvitation } = useDeclineProjectInvitation();

    useEffect(() => {
        setPageAliasRef.current(t("project.invitations.Project invitation"));
    }, [setPageAliasRef, t]);

    useEffect(() => {
        if (!token) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        getProjectInvitationMutate(
            { token },
            {
                onSuccess: (data) => {
                    setProjectTitle(data.project.title);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_404_NOT_FOUND]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { replace: true }),
                        },
                    });

                    handle(error);
                },
            }
        );
    }, []);

    const accept = () => {
        if (!token) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        if (isValidating) {
            return;
        }

        setIsValidating(true);

        acceptProjectInvitation(
            { token },
            {
                onSuccess: (data) => {
                    navigate(ROUTES.BOARD.MAIN(data.project_uid));
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_406_NOT_ACCEPTABLE]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    const decline = () => {
        if (!token) {
            navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND));
            return;
        }

        if (isValidating) {
            return;
        }

        setIsValidating(true);

        declineProjectInvitation(
            { token },
            {
                onSuccess: () => {
                    navigate(ROUTES.DASHBOARD.PROJECTS.ALL);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_406_NOT_ACCEPTABLE]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    return (
        <>
            {projectTitle && (
                <FormOnlyLayout size="sm" useLogo>
                    <h2 className="text-center text-lg font-normal xs:text-2xl">
                        <Trans
                            i18nKey={"project.invitations.You have been invited to join <highlight>{projectTitle}</highlight>"}
                            values={{ projectTitle: projectTitle }}
                            components={{ highlight: <Box as="span" weight="semibold" /> }}
                        />
                    </h2>
                    <p className="mt-8 text-base xs:text-lg">{t("project.invitations.Do you want to join this project?")}&nbsp;</p>
                    <Flex justify="center" mt="8" gap="1">
                        <Button variant="destructive" onClick={decline}>
                            {t("project.invitations.Decline")}
                        </Button>
                        <Button onClick={accept}>{t("project.invitations.Aceept")}</Button>
                    </Flex>
                </FormOnlyLayout>
            )}
        </>
    );
}

export default BoardInvitationPage;
