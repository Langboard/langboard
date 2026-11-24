import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import { useBoard } from "@/core/providers/BoardProvider";
import { ROUTES } from "@/core/routing/constants";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface ArchivePage {
    cards: { uid: string; title: string }[];
    total_count: number;
    next_cursor: string | null;
}

/** Archive pages stay outside the active board model and load only on explicit request. */
export default function BoardArchivedCards({ onClose }: { onClose: () => void }) {
    const [t] = useTranslation();
    const { project, navigateWithFilters } = useBoard();
    const visibleDays = project.useField("archive_visible_days") ?? 3;
    const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
    const cursor = cursors[cursors.length - 1];
    const page = useQuery({
        queryKey: ["get-archived-cards", project.uid, cursor],
        queryFn: async ({ signal }): Promise<ArchivePage> => {
            const response = await api.get(`/board/${project.uid}/cards/archive`, { params: { limit: 25, cursor }, signal });
            return response.data;
        },
        retry: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
    });

    return (
        <Dialog.Root
            open
            onOpenChange={(open) => {
                if (!open) onClose();
            }}
        >
            <Dialog.Content className="flex max-h-[calc(100dvh-2rem)] flex-col gap-3 sm:max-w-xl">
                <Dialog.Header>
                    <Dialog.Title>{t("project.View all archived cards")}</Dialog.Title>
                    <Dialog.Description>
                        {t("project.Archived cards are hidden from the default board after {{days}} days, not deleted.", {
                            days: visibleDays,
                        })}
                    </Dialog.Description>
                </Dialog.Header>
                <div className="min-h-0 overflow-y-auto" aria-busy={page.isFetching}>
                    {page.isPending && <p role="status">{t("common.Loading...")}</p>}
                    {page.isError && (
                        <Button variant="outline" onClick={() => void page.refetch()}>
                            {t("common.Retry")}
                        </Button>
                    )}
                    {page.data && (
                        <>
                            <p className="mb-2 text-xs text-muted-foreground">
                                {t("project.Archived cards ({{count}})", { count: page.data.total_count })}
                            </p>
                            <ul className="space-y-1">
                                {page.data.cards.map((card) => (
                                    <li key={card.uid}>
                                        <Button
                                            variant="ghost"
                                            className="h-auto w-full justify-start whitespace-normal break-words py-2 text-left"
                                            onClick={() => {
                                                onClose();
                                                navigateWithFilters(ROUTES.BOARD.CARD(project.uid, card.uid));
                                            }}
                                        >
                                            {card.title}
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                        </>
                    )}
                </div>
                <div className="flex shrink-0 justify-between gap-2">
                    <Button
                        variant="outline"
                        disabled={cursors.length === 1 || page.isFetching}
                        onClick={() => setCursors((values) => values.slice(0, -1))}
                    >
                        {t("common.Back")}
                    </Button>
                    <Button
                        variant="outline"
                        disabled={!page.data?.next_cursor || page.isFetching}
                        onClick={() => setCursors((values) => [...values, page.data!.next_cursor!])}
                    >
                        {t("common.Next")}
                    </Button>
                </div>
            </Dialog.Content>
        </Dialog.Root>
    );
}
