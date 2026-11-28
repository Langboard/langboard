/** Restore the exact pre-request order if an optimistic reorder fails. */
export function applyOptimisticOrder<T extends { order: number }>(original: readonly T[], reordered: T[]) {
    const previous = original.map((item) => ({ item, order: item.order }));
    const slots = previous.map(({ order }) => order).sort((a, b) => a - b);
    reordered.forEach((item, index) => {
        item.order = slots[index];
    });
    return {
        list: reordered,
        undo: () => {
            previous.forEach(({ item, order }) => {
                item.order = order;
            });
            return original;
        },
    };
}
