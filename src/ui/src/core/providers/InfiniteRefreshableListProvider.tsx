import { TGetListForm } from "@/controllers/api/shared/types";
import useGetInfiniteRefreshableList, { IUseGetInfiniteRefreshableListProps } from "@/controllers/api/shared/useGetInfiniteRefreshableList";
import { TCreatedAtModel, TCreatedAtModelName } from "@/core/models/ModelRegistry";
import { Utils } from "@langboard/core/utils";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

export interface IInfiniteRefreshableListContext<TModelName extends TCreatedAtModelName> {
    models: TCreatedAtModel<TModelName>[];
    listIdRef: React.RefObject<string>;
    isFetchingRef: React.RefObject<bool>;
    isDelayingCheckOutdated: React.RefObject<bool>;
    delayCheckOutdatedTimeoutRef: React.RefObject<NodeJS.Timeout | null>;
    isLastPage: bool;
    countNewRecords: number;
    isRefreshing: bool;
    nextPage: () => Promise<bool>;
    refreshList: () => void;
    checkOutdated: () => Promise<void>;
    checkOutdatedOnScroll: (e: React.UIEvent<HTMLDivElement>) => Promise<void>;
}

interface IInfiniteRefreshableListProviderProps<TModelName extends TCreatedAtModelName> {
    models: TCreatedAtModel<TModelName>[];
    form: TGetListForm<TModelName>;
    limit: number;
    prepareData?: IUseGetInfiniteRefreshableListProps<TModelName>["prepareData"];
    children: React.ReactNode;
}

const initialContext = {
    models: [],
    listIdRef: { current: "" },
    isFetchingRef: { current: false },
    isDelayingCheckOutdated: { current: false },
    delayCheckOutdatedTimeoutRef: { current: null },
    isLastPage: true,
    countNewRecords: 0,
    isRefreshing: false,
    nextPage: async () => false,
    refreshList: async () => {},
    checkOutdated: async () => {},
    checkOutdatedOnScroll: async () => {},
};

const InfiniteRefreshableListContext = createContext<IInfiniteRefreshableListContext<TCreatedAtModelName>>(initialContext);

export function InfiniteRefreshableListProvider<TModelName extends TCreatedAtModelName>({
    models: flatModels,
    form,
    limit,
    prepareData,
    children,
}: IInfiniteRefreshableListProviderProps<TModelName>): React.ReactNode {
    const [page, setPage] = useState(0);
    const [isLastPage, setIsLastPage] = useState(!flatModels.length);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const models = useMemo(
        () => flatModels.sort((a, b) => b.created_at.getTime() - a.created_at.getTime()).slice(0, limit * (page + 1)),
        [flatModels, page]
    );
    const lastCurrentDateRef = useRef<Date>(models[0] ? new Date(models[0].created_at) : new Date());
    const {
        mutateAsync,
        refresh,
        checkOutdated: originalCheckOutdated,
        countNewRecords,
    } = useGetInfiniteRefreshableList({ form, limit, setPage, isLastPage, setIsLastPage, lastCurrentDateRef, prepareData });
    const isDelayingCheckOutdated = useRef(false);
    const isFetchingRef = useRef(false);
    const listIdRef = useRef(Utils.String.Token.shortUUID());
    const delayCheckOutdatedTimeoutRef = useRef<NodeJS.Timeout>(null);
    const nextPage = useCallback(async () => {
        if (isFetchingRef.current || isLastPage) {
            return false;
        }

        isFetchingRef.current = true;
        try {
            if (flatModels.length <= limit * (page + 1)) {
                await mutateAsync({ page });
            }
            setPage((prev) => prev + 1);
            return true;
        } finally {
            isFetchingRef.current = false;
        }
    }, [page, setPage, isLastPage, mutateAsync, flatModels]);
    const refreshList = useCallback(() => {
        if (isFetchingRef.current || isRefreshing) {
            return;
        }

        setIsRefreshing(true);
        const curIsDelayingCheckOutdated = isDelayingCheckOutdated.current;
        isDelayingCheckOutdated.current = false;
        const list = document.getElementById(listIdRef.current)!;

        list.scrollTo({
            top: 0,
            behavior: "smooth",
        });

        isFetchingRef.current = true;
        void refresh()
            .catch(() => {})
            .finally(() => {
                isFetchingRef.current = false;
                if (!delayCheckOutdatedTimeoutRef.current && curIsDelayingCheckOutdated) {
                    isDelayingCheckOutdated.current = false;
                } else {
                    isDelayingCheckOutdated.current = curIsDelayingCheckOutdated;
                }
                setIsRefreshing(false);
                list.scrollTop = 0;
            });
    }, [refresh, isRefreshing]);
    const checkOutdated = useCallback(async () => {
        await originalCheckOutdated(lastCurrentDateRef.current);
    }, [originalCheckOutdated]);
    const checkOutdatedOnScroll = useCallback(
        async (e: React.UIEvent<HTMLDivElement>) => {
            const target = e.target as HTMLElement;
            if (isFetchingRef.current || isDelayingCheckOutdated.current || target.scrollTop < target.scrollHeight * 0.3) {
                return;
            }

            isDelayingCheckOutdated.current = true;

            try {
                await checkOutdated();
            } catch {
                // The API interceptor reports failures; release the scroll-check gate.
            } finally {
                delayCheckOutdatedTimeoutRef.current = setTimeout(() => {
                    isDelayingCheckOutdated.current = false;
                    delayCheckOutdatedTimeoutRef.current = null;
                }, 10000);
            }
        },
        [checkOutdated]
    );

    useEffect(() => {
        if (page) {
            return;
        }

        if (flatModels.length > 0) {
            void nextPage().catch(() => {});
            return;
        }

        lastCurrentDateRef.current = new Date();
        const initialLoad = setTimeout(() => {
            void mutateAsync({ page: 0 })
                .then(() => setPage(1))
                .catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            setPage(0);
        };
    }, []);

    return (
        <InfiniteRefreshableListContext.Provider
            value={{
                isFetchingRef,
                isDelayingCheckOutdated,
                delayCheckOutdatedTimeoutRef,
                checkOutdated,
                models,
                nextPage,
                refreshList,
                isRefreshing,
                listIdRef,
                isLastPage,
                countNewRecords,
                checkOutdatedOnScroll,
            }}
        >
            {children}
        </InfiniteRefreshableListContext.Provider>
    );
}

export function useInfiniteRefreshableList<TModelName extends TCreatedAtModelName>(): IInfiniteRefreshableListContext<TModelName> {
    const context = useContext(InfiniteRefreshableListContext);
    if (!context) {
        throw new Error("useInfiniteRefreshableList must be used within an InfiniteRefreshableListProvider");
    }
    return context as IInfiniteRefreshableListContext<TModelName>;
}
