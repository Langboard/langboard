import CardPriorityPopover from "./CardPriorityPopover";
import useChangeCardDetails from "@/controllers/api/card/useChangeCardDetails";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { queryClient } from "@/core/helpers/QueryClient";

export default function BoardCardPriority({ onOpenChange, effectsEnabled }: { onOpenChange?: (open: boolean) => void; effectsEnabled?: boolean }) {
    const { card, canEditCard, projectUID } = useBoardCard();
    const priority = card.useField("priority") ?? "P2";
    const { mutateAsync } = useChangeCardDetails();
    return (
        <CardPriorityPopover
            key={`${projectUID}:${card.uid}`}
            value={priority}
            disabled={!canEditCard || Boolean(card.source_type && card.source_uid)}
            onOpenChange={onOpenChange}
            effectsEnabled={effectsEnabled}
            save={async (next) => {
                await mutateAsync({ project_uid: projectUID, card_uid: card.uid, priority: next });
                // Confirm through the current authorized response; sockets may already have delivered a newer value.
                await Promise.all([
                    queryClient.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${card.uid}`] }),
                    queryClient.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] }),
                ]);
            }}
        />
    );
}
