import { useCallback, useEffect } from "react";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Loading from "@/components/base/Loading";
import Skeleton from "@/components/base/Skeleton";
import { Utils } from "@langboard/core/utils";
import InfiniteScroller from "@/components/InfiniteScroller";
import useGetTrackingList from "@/controllers/api/dashboard/useGetTrackingList";
import { useTranslation } from "react-i18next";
import TrackingRow from "@/pages/DashboardPage/components/TrackingRow";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ProjectCheckitem } from "@/core/models";

export function SkeletonTrackingPage(): React.JSX.Element {
    return (
        <>
            {Array.from({ length: 4 }).map((_, index) => (
                <Flex items="center" w="full" key={Utils.String.Token.shortUUID()} mt={index ? "2" : "0"}>
                    <Box px="4" className="w-1/4">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/4">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
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

function TrackingPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const { mutateAsync, checkitemUIDs, isLastPage, isFetchingRef } = useGetTrackingList();
    const checkitems = ProjectCheckitem.Model.useModels((model) => checkitemUIDs.includes(model.uid), [checkitemUIDs]);
    const nextPage = useCallback(async () => {
        if (isFetchingRef.current || isLastPage) {
            return false;
        }

        await mutateAsync({});
        return true;
    }, [isLastPage, mutateAsync]);

    useEffect(() => {
        setPageAliasRef.current("Dashboard");
    }, [mutateAsync, checkitems]);

    return (
        <>
            <InfiniteScroller.Table.Default
                columns={[
                    { name: t("dashboard.Checkitem"), className: "w-1/4 text-center" },
                    { name: t("dashboard.Card"), className: "w-1/4 text-center" },
                    { name: t("dashboard.Status"), className: "w-1/6 text-center" },
                    { name: t("dashboard.Started at"), className: "w-1/6 text-center" },
                    { name: t("dashboard.Time taken"), className: "w-1/6 text-center" },
                ]}
                scrollable={() => document.getElementById("main")}
                loadMore={nextPage}
                hasMore={!isLastPage}
                totalCount={checkitems.length}
                loader={
                    <Flex justify="center" mt={{ initial: "4", md: "6", lg: "8" }} key={Utils.String.Token.shortUUID()}>
                        <Loading size="3" variant="secondary" />
                    </Flex>
                }
            >
                {checkitems.map((checkitem) => (
                    <TrackingRow checkitem={checkitem} key={`tracking-list-${checkitem.uid}`} />
                ))}
            </InfiniteScroller.Table.Default>
            {!checkitems.length && (
                <Flex justify="center" items="center" h="full" mt="2">
                    {t("dashboard.No checkitems")}
                </Flex>
            )}
        </>
    );
}

export default TrackingPage;
