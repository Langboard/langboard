import { useCallback, useEffect } from "react";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Loading from "@/components/base/Loading";
import Skeleton from "@/components/base/Skeleton";
import { Utils } from "@langboard/core/utils";
import InfiniteScroller from "@/components/InfiniteScroller";
import useGetDashboardCards from "@/controllers/api/dashboard/useGetDashboardCards";
import { useTranslation } from "react-i18next";
import CardRow from "@/pages/DashboardPage/components/CardRow";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ProjectCard } from "@/core/models";

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
    const { mutateAsync, cardUIDs, isLastPage, isFetchingRef } = useGetDashboardCards();
    const cards = ProjectCard.Model.useModels((model) => cardUIDs.includes(model.uid), [cardUIDs]);
    const nextPage = useCallback(async () => {
        if (isFetchingRef.current || isLastPage) {
            return false;
        }

        await mutateAsync({});
        return true;
    }, [isLastPage, mutateAsync]);

    useEffect(() => {
        setPageAliasRef.current("Dashboard");
    }, [mutateAsync, cards]);

    return (
        <>
            <InfiniteScroller.Table.Default
                columns={[
                    { name: t("dashboard.Title"), className: "w-1/3 text-center" },
                    { name: t("dashboard.Column"), className: "w-1/3 text-center" },
                    { name: t("dashboard.Started at"), className: "w-1/6 text-center" },
                    { name: t("dashboard.Time taken"), className: "w-1/6 text-center" },
                ]}
                scrollable={() => document.getElementById("main")}
                loadMore={nextPage}
                hasMore={!isLastPage}
                totalCount={cards.length}
                loader={
                    <Flex justify="center" mt={{ initial: "4", md: "6", lg: "8" }} key={Utils.String.Token.shortUUID()}>
                        <Loading size="3" variant="secondary" />
                    </Flex>
                }
            >
                {cards.map((card) => (
                    <CardRow card={card} key={`cards-list-${card.uid}`} />
                ))}
            </InfiniteScroller.Table.Default>
            {!cards.length && (
                <Flex justify="center" items="center" h="full" mt="2">
                    {t("dashboard.No cards")}
                </Flex>
            )}
        </>
    );
}

export default CardsPage;
