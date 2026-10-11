import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { isAxiosError } from "axios";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { api } from "@/core/helpers/Api";
import { InteractionForm, type CardInteraction } from "../card/CardAppInteractions";

export interface PanelInteractionTarget {
    card_uid: string;
    interaction_uid: string;
    app_revision: string;
    binding_revision: string;
}
export default function PanelInteractionDialog({
    projectUID,
    userUID,
    appKey,
    target,
    onClose,
    onAuthorityDenied,
}: {
    projectUID: string;
    userUID: string;
    appKey: string;
    target: PanelInteractionTarget;
    onClose: () => void;
    onAuthorityDenied: () => void;
}) {
    const [t] = useTranslation();
    const [sending, setSending] = useState(false);
    const path = `/board/${projectUID}/apps/${encodeURIComponent(appKey)}/panel`;
    const query = useQuery({
        queryKey: ["panel-human-interaction", userUID, projectUID, appKey, target],
        queryFn: async ({ signal }) =>
            (
                await api.post<{ items: CardInteraction[]; card_revision: number }>(
                    `${path}/interactions`,
                    { ...target, limit: 1 },
                    { signal, env: { interceptToast: true } as never }
                )
            ).data,
        gcTime: 0,
        retry: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    useEffect(() => {
        if (isAxiosError(query.error) && [401, 403, 404, 409].includes(query.error.response?.status ?? 0)) onAuthorityDenied();
    }, [query.error, onAuthorityDenied]);
    const item = query.data?.items.find((item) => item.interaction_uid === target.interaction_uid && item.source?.app_key === appKey);
    return (
        <Dialog.Root
            open
            onOpenChange={(open) => {
                if (!open && !sending) onClose();
            }}
        >
            <Dialog.Content className="max-w-lg" withCloseButton={!sending} disableOverlayClick={sending}>
                <Dialog.Header>
                    <Dialog.Title>{t("card.appInteractions.title")}</Dialog.Title>
                    <Dialog.Description>{t("card.appInteractions.help")}</Dialog.Description>
                </Dialog.Header>
                <Button size="sm" variant="outline" disabled={query.isFetching || sending} onClick={() => void query.refetch()}>
                    {t("card.appInteractions.refresh")}
                </Button>
                {query.isFetching && <p role="status">{t("common.Loading...")}</p>}
                {(query.isError || (!query.isFetching && !item)) && <p role="alert">{t("card.appInteractions.unavailable")}</p>}
                {item && !query.isError && !query.isFetching && (
                    <InteractionForm
                        key={`${item.interaction_uid}/${item.revision}/${query.dataUpdatedAt}`}
                        item={item}
                        path={`${path}/cards/${target.card_uid}/interactions`}
                        responseContext={{ app_revision: target.app_revision, binding_revision: target.binding_revision }}
                        canRespond={!query.isFetching && !sending}
                        onBusy={setSending}
                        cardRevision={query.data!.card_revision}
                        refresh={() => query.refetch()}
                    />
                )}
            </Dialog.Content>
        </Dialog.Root>
    );
}
