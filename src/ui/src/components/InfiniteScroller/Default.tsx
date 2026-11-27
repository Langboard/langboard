import { forwardRef, useEffect } from "react";
import { cn } from "@/core/utils/ComponentUtils";
import useInfiniteScrollerVirtualizer from "@/components/InfiniteScroller/useInfiniteScrollerVirtualizer";
import { TSharedInfiniteScrollerProps } from "@/components/InfiniteScroller/types";
import { composeRefs } from "@/core/utils/ComponentUtils";
import { useVirtualizer } from "@tanstack/react-virtual";
import Box from "@/components/base/Box";

export interface IDefaultInfiniteScrollerProps extends TSharedInfiniteScrollerProps<React.ReactElement> {
    as?: React.ElementType;
    row?: React.ElementType;
    outerToApplyHeightRef?: React.RefObject<HTMLElement | null>;
    rowClassName?: string;
    totalCount: number;
    virtualizerRef?: React.RefObject<ReturnType<typeof useVirtualizer>>;
}

const DefaultInfiniteScroller = forwardRef<HTMLElement, IDefaultInfiniteScrollerProps>(
    (
        {
            hasMore,
            initialLoad,
            loadMore,
            pageStart,
            loader,
            loaderClassName,
            scrollable,
            gap,
            as = "div",
            row = "div",
            outerToApplyHeightRef,
            rowClassName,
            className,
            totalCount,
            virtualizerRef,
            children,
            ...props
        },
        ref
    ) => {
        const { setLoaderRef, items, virtualizer } = useInfiniteScrollerVirtualizer({
            hasMore,
            initialLoad,
            loadMore,
            pageStart,
            loader,
            scrollable,
            gap,
            totalCount,
            virtualizerRef,
            children,
        });

        const virtualItems = virtualizer.getVirtualItems();
        const loaderItemIndex = hasMore ? items.length - 1 : -1;
        const loaderItem = virtualItems.find((virtualItem) => virtualItem.index === loaderItemIndex);
        const loaderIndex = hasMore ? (loaderItem?.index ?? "-1") : "-1";
        const loaderY = hasMore ? (loaderItem?.start ?? -99999) : -99999;

        useEffect(() => {
            const outer = outerToApplyHeightRef?.current;
            if (!outer) {
                return;
            }

            setTimeout(() => {
                outer.style.height = `${virtualizer.getTotalSize()}px`;
            }, 0);
        }, [virtualizer.getTotalSize, children]);

        const Comp = as;
        const RowComp = row;
        return (
            <Comp {...props} className={cn(className, "relative")} style={{ ...props.style, height: `${virtualizer.getTotalSize()}px` }} ref={ref}>
                {virtualItems.map((virtualRow) => {
                    if (hasMore && virtualRow.index === loaderItemIndex) {
                        return null;
                    }

                    return (
                        <RowComp
                            key={virtualRow.index}
                            className={cn(rowClassName, "absolute left-0 top-0")}
                            data-index={virtualRow.index}
                            style={{ transform: `translateY(${virtualRow.start}px)` }}
                            ref={virtualizer.measureElement}
                        >
                            {items[virtualRow.index]}
                        </RowComp>
                    );
                })}
                <Box
                    className={cn(loaderClassName, "absolute left-0 top-0 w-full", !hasMore && "hidden")}
                    data-index={loaderIndex}
                    style={{
                        transform: `translateY(${loaderY}px)`,
                    }}
                    ref={composeRefs<HTMLDivElement>(setLoaderRef, virtualizer.measureElement)}
                >
                    {loader}
                </Box>
            </Comp>
        );
    }
);

export default DefaultInfiniteScroller;
