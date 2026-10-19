import { TGetListForm } from "@/controllers/api/shared/types";
import useGetPaginatedList, { IUseGetPaginatedListProps } from "@/controllers/api/shared/useGetPaginatedList";
import { TCreatedAtModel, TCreatedAtModelName } from "@/core/models/ModelRegistry";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export interface IPaginatedListContext<TModelName extends TCreatedAtModelName> {
    models: TCreatedAtModel<TModelName>[];
    isFetchingRef: React.RefObject<bool>;
    countNewRecords: number;
    isRefreshing: bool;
    status: "error" | "idle" | "pending" | "success";
    refreshList: () => void;
    checkOutdated: () => Promise<void>;
}

interface IPaginatedListProviderProps<TModelName extends TCreatedAtModelName> {
    models: TCreatedAtModel<TModelName>[];
    form: TGetListForm<TModelName>;
    limit: number;
    prepareData?: IUseGetPaginatedListProps<TModelName>["prepareData"];
    children: React.ReactNode;
}

const initialContext = {
    models: [],
    isFetchingRef: { current: false },
    countNewRecords: 0,
    isRefreshing: false,
    status: "idle" as const,
    refreshList: () => {},
    checkOutdated: async () => {},
};

const PaginatedListContext = createContext<IPaginatedListContext<TCreatedAtModelName>>(initialContext);

export function PaginatedListProvider<TModelName extends TCreatedAtModelName>({
    models,
    form,
    limit,
    prepareData,
    children,
}: IPaginatedListProviderProps<TModelName>): React.ReactNode {
    const [isRefreshing, setIsRefreshing] = useState(false);
    const lastCurrentDateRef = useRef<Date>(
        models[0] ? new Date(models.sort((a, b) => b.created_at.getTime() - a.created_at.getTime())[0].created_at) : new Date()
    );
    const {
        mutateAsync,
        refresh,
        countNewRecords,
        checkOutdated: originalCheckOutdated,
        status,
    } = useGetPaginatedList({
        form,
        limit,
        lastCurrentDateRef,
        prepareData,
    });
    const isFetchingRef = useRef(false);
    const refreshList = useCallback(() => {
        if (isFetchingRef.current || isRefreshing) {
            return;
        }

        setIsRefreshing(true);

        isFetchingRef.current = true;
        void refresh()
            .catch(() => {})
            .finally(() => {
                isFetchingRef.current = false;
                setIsRefreshing(false);
            });
    }, [refresh, isRefreshing]);
    const checkOutdated = useCallback(async () => {
        await originalCheckOutdated(lastCurrentDateRef.current);
    }, [originalCheckOutdated]);

    useEffect(() => {
        lastCurrentDateRef.current = new Date();
        const initialLoad = setTimeout(() => {
            void mutateAsync({ page: 0 }).catch(() => {});
        }, 0);
        return () => clearTimeout(initialLoad);
    }, []);

    return (
        <PaginatedListContext.Provider
            value={{
                isFetchingRef,
                models,
                refreshList,
                checkOutdated,
                isRefreshing,
                countNewRecords,
                status,
            }}
        >
            {children}
        </PaginatedListContext.Provider>
    );
}

export function usePaginatedList<TModelName extends TCreatedAtModelName>(): IPaginatedListContext<TModelName> {
    const context = useContext(PaginatedListContext);
    if (!context) {
        throw new Error("usePaginatedList must be used within an PaginatedListProvider");
    }
    return context as IPaginatedListContext<TModelName>;
}
