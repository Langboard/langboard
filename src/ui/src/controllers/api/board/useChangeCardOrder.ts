import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";

export interface IChangeCardOrderForm {
    project_uid: string;
    card_uid: string;
    parent_uid?: string;
    order: number;
}

const useChangeCardOrder = (options?: TMutationOptions<IChangeCardOrderForm>) => {
    const { mutate } = useQueryMutation();
    const queryClient = useQueryClient();
    const [confirmationOpen, setConfirmationOpen] = useState(false);
    const busy = useRef(false);
    useEffect(
        () => () => {
            decide.current?.(null);
        },
        []
    );
    const decide = useRef<((value: boolean | null) => void) | null>(null);
    const confirmDescendants = () =>
        new Promise<boolean | null>((resolve) => {
            decide.current = resolve;
            setConfirmationOpen(true);
        });
    const resolveConfirmation = (value: boolean | null) => {
        decide.current?.(value);
        decide.current = null;
        setConfirmationOpen(false);
    };

    const changeCardOrder = async (params: IChangeCardOrderForm) => {
        if (busy.current) throw new Error("A card movement is already pending");
        busy.current = true;
        try {
            const url = Utils.String.format(Routing.API.BOARD.CARD.CHANGE_ORDER, { uid: params.project_uid, card_uid: params.card_uid });
            let decision: boolean | null | undefined;
            let revision: string | undefined;
            for (;;) {
                const res = await api.put(
                    url,
                    {
                        parent_uid: params.parent_uid,
                        order: params.order,
                        move_descendants: decision,
                        confirmation_revision: revision,
                    },
                    { env: { interceptToast: options?.interceptToast } as never }
                );
                if (!res.data?.confirmation_required) {
                    await queryClient.invalidateQueries({ queryKey: [`get-cards-${params.project_uid}`] });
                    return res.data;
                }
                decision = await confirmDescendants();
                if (decision === null) throw new Error("Card movement cancelled");
                revision = res.data.confirmation_revision;
            }
        } finally {
            busy.current = false;
        }
    };

    const result = mutate(["change-card-order"], changeCardOrder, {
        ...options,
        retry: 0,
    });

    return { ...result, confirmation: { open: confirmationOpen, onDecision: resolveConfirmation } };
};

export default useChangeCardOrder;
