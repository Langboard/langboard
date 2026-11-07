import { useEffect, useMemo, useReducer, useRef, useState } from "react";

interface IUseInfiniteScrollPagerProps<T> {
    allItems: T[];
    size: number;
    updater?: [unknown, React.DispatchWithoutAction];
    pagination?: [number, React.Dispatch<React.SetStateAction<number>>];
}

function useInfiniteScrollPager<T>({ allItems, size, updater, pagination }: IUseInfiniteScrollPagerProps<T>) {
    const [updated, forceUpdate] = updater ?? useReducer((x) => x + 1, 0);
    const localPagination = useState(1);
    const [page, setPage] = pagination ?? localPagination;
    const items = useMemo<T[]>(() => {
        return allItems.slice(0, page * size);
    }, [allItems, page, updated]);
    const lastPageRef = useRef(1);
    lastPageRef.current = Math.max(1, Math.ceil(allItems.length / size));
    const hasMore = page < lastPageRef.current && allItems.length > size;

    const nextPage = (next: number) => {
        if (next - page > 1) {
            return false;
        }

        return new Promise<bool>((resolve) => {
            setTimeout(() => {
                setPage(next);
                resolve(true);
            }, 500);
        });
    };

    const toLastPage = () => {
        setPage(lastPageRef.current);
    };

    useEffect(() => {
        forceUpdate();
    }, [page]);

    return {
        updated,
        forceUpdate,
        page,
        items,
        lastPageRef,
        nextPage,
        toLastPage,
        hasMore,
    };
}

export default useInfiniteScrollPager;
