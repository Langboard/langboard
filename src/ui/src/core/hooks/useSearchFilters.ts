/* eslint-disable @typescript-eslint/no-explicit-any */
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { Utils } from "@langboard/core/utils";
import { useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { immer } from "zustand/middleware/immer";

export interface ISearchFilterMap {
    [key: string]: string[] | undefined;
}

export interface IUseSearchFiltersProps<TFilterMap extends ISearchFilterMap> {
    filterKeys: (keyof TFilterMap)[];
    searchKey?: string | "filters";
}

const updatedStore = create(
    immer<{ updated: number; forceUpdate: () => void }>((set) => ({
        updated: 0,
        forceUpdate: () => {
            set((state) => {
                state.updated += 1;
            });
        },
    }))
);

const useSearchFilters = <TFilterMap extends ISearchFilterMap>(
    { filterKeys, searchKey = "filters" }: IUseSearchFiltersProps<TFilterMap>,
    deps?: unknown[]
) => {
    const { locationPopped } = usePageHeader();
    const location = usePageLocation();
    const [updated, setUpdated] = useState(updatedStore.getState().updated);
    const filters = useMemo(() => {
        const searchParams = new URLSearchParams(location.search);
        const rawFilters = searchParams.get(searchKey);
        const newFilters = fromString(rawFilters);
        return newFilters;
    }, [location, location.search, locationPopped, updated, ...(deps || [])]);
    const filtersRef = useRef<TFilterMap>({} as TFilterMap);
    filtersRef.current = filters;

    const forceUpdate = () => {
        updatedStore.getState().forceUpdate();
        setUpdated(updatedStore.getState().updated);
    };

    function toString(): string {
        return Object.entries(filtersRef.current)
            .map(([key, value]) => {
                if (!value!.length) {
                    return "";
                }
                return `${key as string}:${encodeURIComponent(encodeURIComponent(value!.join(",")))}`;
            })
            .join(",");
    }

    function fromString(rawFilters: string | null): TFilterMap {
        const filterMap = {} as TFilterMap;

        if (rawFilters) {
            rawFilters.split(",").map((rawFilter) => {
                const [key, value] = rawFilter.split(":");
                if (Utils.Type.isNull(value) || Utils.Type.isUndefined(value) || !value.length) {
                    return;
                }

                if (!filterKeys.includes(key)) {
                    return;
                }

                filterMap[key as keyof TFilterMap] = decodeURIComponent(decodeURIComponent(value)).split(",") as any;
            });
        }

        return filterMap;
    }

    function unique() {
        for (let i = 0; i < filterKeys.length; ++i) {
            const filterKey = filterKeys[i];
            const filterValue = filtersRef.current[filterKey];
            if (!filterValue?.length) {
                continue;
            }

            filtersRef.current[filterKey] = filterValue.filter((value, index) => filterValue.indexOf(value) === index) as any;
        }
    }

    useEffect(() => {
        const unsub = updatedStore.subscribe(() => {
            setTimeout(() => {
                setUpdated(updatedStore.getState().updated);
            }, 0);
        });

        return unsub;
    }, [updated]);

    return {
        filters,
        toString,
        fromString,
        unique,
        updated,
        forceUpdate,
    };
};

export default useSearchFilters;
