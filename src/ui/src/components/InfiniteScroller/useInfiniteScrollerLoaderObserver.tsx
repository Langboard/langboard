import { IUseInfiniteScrollerLoaderObserverProps } from "@/components/InfiniteScroller/types";
import { useCallback, useEffect, useRef, useState } from "react";

const useInfiniteScrollerLoaderObserver = ({
    hasMore = false,
    initialLoad = true,
    loadMore,
    pageStart = 1,
    loader,
    scrollable,
    children,
}: IUseInfiniteScrollerLoaderObserverProps) => {
    const pageRef = useRef(pageStart);
    const loaderRef = useRef<HTMLDivElement | null>(null);
    const canLoadRef = useRef(true);
    const observerRef = useRef<IntersectionObserver | null>(null);
    const [isFirstLoaded, setIsFirstLoaded] = useState(!initialLoad);
    const triggerLoad = useCallback(async () => {
        if (!canLoadRef.current || !hasMore) {
            return;
        }
        canLoadRef.current = false;
        const next = pageRef.current + 1;
        try {
            if (await loadMore(next)) {
                pageRef.current = next;
            }
        } catch {
            // The request owner reports the error; retain this page for a later attempt.
        } finally {
            setTimeout(() => {
                canLoadRef.current = true;
            }, 0);
        }
    }, [hasMore, loadMore]);
    const setLoaderRef = useCallback(
        (node: HTMLDivElement | null) => {
            observerRef.current?.disconnect();
            observerRef.current = null;

            loaderRef.current = node;
            if (!node) {
                return;
            }

            const rootElem = scrollable();
            if (!rootElem || !hasMore || !isFirstLoaded) {
                return;
            }

            observerRef.current = new IntersectionObserver(
                (entries) => {
                    if (!isFirstLoaded) {
                        return;
                    }

                    if (entries[0].isIntersecting) {
                        triggerLoad();
                    }
                },
                { root: rootElem, threshold: 0.1 }
            );
            observerRef.current.observe(node);
        },
        [triggerLoad, scrollable, hasMore, isFirstLoaded]
    );

    useEffect(() => {
        if (initialLoad && pageRef.current === pageStart && !isFirstLoaded) {
            triggerLoad().finally(() => {
                setIsFirstLoaded(() => true);
            });
            return;
        }

        return () => {
            observerRef.current?.disconnect();
            observerRef.current = null;
        };
    }, []);

    let items = Array.isArray(children) ? children : [children];
    if (hasMore) {
        items = [...items, loader];
    }

    return {
        setLoaderRef,
        items,
    };
};

export default useInfiniteScrollerLoaderObserver;
