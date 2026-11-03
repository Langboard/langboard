import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Loading from "@/components/base/Loading";
import Skeleton from "@/components/base/Skeleton";
import { Utils } from "@langboard/core/utils";
import DataTable from "@/components/base/DataTable";
import Button from "@/components/base/Button";
import useGetDashboardCards from "@/controllers/api/dashboard/useGetDashboardCards";
import { useTranslation } from "react-i18next";
import { CardRowCell } from "@/pages/DashboardPage/components/CardRow";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ProjectCard } from "@/core/models";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";

export function SkeletonCardsPage(): React.JSX.Element {
    return (
        <>
            {Array.from({ length: 4 }).map((_, index) => (
                <Flex items="center" w="full" key={Utils.String.Token.shortUUID()} mt={index ? "2" : "0"}>
                    <Box px="4" className="w-1/3">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/3">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                </Flex>
            ))}
        </>
    );
}

function CardsPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const location = usePageLocation();
    const initialLocation = useRef(location);
    const [isTableReady, setIsTableReady] = useState(false);
    const loadInFlight = useRef(false);
    const { mutateAsync, cardUIDs, isLastPage, isFetchingRef } = useGetDashboardCards();
    const models = ProjectCard.Model.useModels((model) => cardUIDs.includes(model.uid), [cardUIDs]);
    const cards = useMemo(() => {
        const byUID = new Map(models.map((card) => [card.uid, card]));
        return cardUIDs.flatMap((uid) => {
            const card = byUID.get(uid);
            return card ? [card] : [];
        });
    }, [models, cardUIDs]);
    const [isLoading, setIsLoading] = useState(true);
    const [hasLoadError, setHasLoadError] = useState(false);
    const nextPage = useCallback(async () => {
        if (loadInFlight.current || isFetchingRef.current) return;
        loadInFlight.current = true;
        setIsLoading(true);
        setHasLoadError(false);
        try {
            await mutateAsync({});
        } catch {
            setHasLoadError(true);
        } finally {
            loadInFlight.current = false;
            setIsLoading(false);
        }
    }, [mutateAsync, isFetchingRef]);

    useEffect(() => {
        // A remount starts a fresh server cursor. Drop inherited display state
        // before mounting the table so stale pages/filters cannot hide results.
        const entry = initialLocation.current;
        navigate(
            { pathname: entry.pathname, search: entry.search, hash: entry.hash },
            {
                replace: true,
                state: { ...entry.state, page: 1, sort: {}, columnFilters: {}, search: "" },
            }
        );
        setIsTableReady(true);
    }, [navigate]);

    useEffect(() => {
        setPageAliasRef.current("Dashboard");
    }, [setPageAliasRef]);

    useEffect(() => {
        // Use the native fetcher for observable initial loading/error state.
        // Its in-flight guard also covers the hook's scheduled initial request.
        void nextPage();
    }, [nextPage]);

    return (
        <Box aria-busy={isLoading} className="min-w-0">
            <DataTable
                data={cards}
                totalRecords={cards.length}
                itemsPerPage={15}
                showPagination
                loading={!isTableReady || (isLoading && !cards.length)}
                emptyMessage={t("dashboard.No cards")}
                emptyIcon=""
                columns={[
                    { key: "title", header: t("dashboard.Title"), render: ({ row }) => <CardRowCell card={row} field="title" /> },
                    { key: "project_column_name", header: t("dashboard.Column"), render: ({ row }) => <CardRowCell card={row} field="column" /> },
                    { key: "created_at", header: t("dashboard.Started at"), render: ({ row }) => <CardRowCell card={row} field="created" /> },
                    { key: "archived_at", header: t("dashboard.Time taken"), render: ({ row }) => <CardRowCell card={row} field="duration" /> },
                ]}
            />
            {(!isLastPage || hasLoadError) && (
                <Flex justify="center" mt="4">
                    <Button className="btn btn-outline" variant="outline" disabled={isLoading} onClick={() => void nextPage()}>
                        {isLoading ? <Loading size="3" variant="secondary" /> : hasLoadError ? t("common.Retry") : t("common.More")}
                    </Button>
                </Flex>
            )}
        </Box>
    );
}

export default CardsPage;
