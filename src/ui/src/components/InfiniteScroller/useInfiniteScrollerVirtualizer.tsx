import { IUseInfiniteScrollerLoaderObserverProps } from "@/components/InfiniteScroller/types";
import useInfiniteScrollerLoaderObserver from "@/components/InfiniteScroller/useInfiniteScrollerLoaderObserver";
import { Utils } from "@langboard/core/utils";
import { useVirtualizer } from "@tanstack/react-virtual";

export interface IUseInfiniteScrollerVirtualizerProps extends IUseInfiniteScrollerLoaderObserverProps {
    scrollable: () => HTMLElement | null;
    loader: React.ReactNode;
    children: React.ReactNode;
    totalCount: number;
    virtualizerRef?: React.RefObject<ReturnType<typeof useVirtualizer> | undefined>;
}

const useInfiniteScrollerVirtualizer = ({
    scrollable,
    loader,
    children,
    hasMore,
    gap,
    totalCount,
    virtualizerRef,
    ...props
}: IUseInfiniteScrollerVirtualizerProps) => {
    const { setLoaderRef, items } = useInfiniteScrollerLoaderObserver({
        scrollable,
        loader,
        hasMore,
        children,
        ...props,
    });
    const itemCount = items.length || totalCount;

    const virtualizer = useVirtualizer({
        count: itemCount,
        getScrollElement: scrollable,
        estimateSize: () => 0,
        measureElement: (el: HTMLElement) => el.getBoundingClientRect().height,
        gap: Utils.Type.isString(gap) ? parseInt(gap) : gap,
        overscan: 5,
    });

    if (virtualizerRef) {
        virtualizerRef.current = virtualizer as unknown as ReturnType<typeof useVirtualizer>;
    }

    return {
        setLoaderRef,
        virtualizer,
        items,
    };
};

export default useInfiniteScrollerVirtualizer;
