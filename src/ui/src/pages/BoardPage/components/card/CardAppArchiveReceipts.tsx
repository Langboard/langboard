import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface ArchiveReceipt {
    archive_uid: string;
    app_key: string;
    receipt: { state: string; external_state?: string | null; outcome_source?: string; card_type?: string };
    delivery: { state: string; attempt_count: number } | null;
}

/** Load bounded archive history only on demand in card detail; never poll every board card. */
export default function CardAppArchiveReceipts({ projectUID, cardUID, userUID }: { projectUID: string; cardUID: string; userUID: string }) {
    const [t] = useTranslation();
    const [opened, setOpened] = useState(false);
    const query = useQuery({
        queryKey: ["card-app-archives", projectUID, cardUID, userUID],
        queryFn: async ({ signal }) =>
            (
                await api.get<{ items: ArchiveReceipt[] }>(`/board/${projectUID}/card/${cardUID}/apps/archives?limit=10`, {
                    signal,
                    env: { interceptToast: true } as never,
                })
            ).data.items,
        enabled: opened,
        retry: 0,
        refetchOnWindowFocus: false,
        gcTime: 0,
    });
    return (
        <div className="space-y-2">
            <Button size="sm" variant="ghost" aria-expanded={opened} onClick={() => setOpened((value) => !value)}>
                {t("card.App archive history", { defaultValue: "App archive history" })}
            </Button>
            {opened && (
                <div className="space-y-2 rounded-md border p-3 text-sm">
                    <p className="text-xs text-muted-foreground">
                        {t("card.App archive receipt help", {
                            defaultValue: "Delivery and provider outcome are separate. Provider outcomes are reported by the authenticated app.",
                        })}
                    </p>
                    {query.isPending && <p role="status">{t("common.Loading...")}</p>}
                    {query.isError && (
                        <p role="alert">{t("card.App archive history unavailable", { defaultValue: "Archive history unavailable." })}</p>
                    )}
                    {query.data?.length === 0 && <p>{t("card.No app archive history", { defaultValue: "No app archive history." })}</p>}
                    {query.data?.map((item) => (
                        <div key={item.archive_uid} className="space-y-1 rounded-md border p-2">
                            <p className="font-medium">
                                {item.app_key} · {item.receipt.card_type}
                            </p>
                            <p>
                                {t("card.Archive outcome", { defaultValue: "Archive outcome" })}:{" "}
                                {t(`card.App archive state ${item.receipt.state}`, { defaultValue: item.receipt.state })}
                            </p>
                            {item.delivery && (
                                <p>
                                    {t("card.Event delivery", { defaultValue: "Event delivery" })}:{" "}
                                    {t(`card.App delivery state ${item.delivery.state}`, { defaultValue: item.delivery.state })} ·{" "}
                                    {item.delivery.attempt_count}
                                </p>
                            )}
                            {item.receipt.external_state && (
                                <p>
                                    {t("card.Provider reported state", { defaultValue: "Provider reported state" })}: {item.receipt.external_state}
                                </p>
                            )}
                        </div>
                    ))}
                    <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
                        {t("common.Refresh", { defaultValue: "Refresh" })}
                    </Button>
                </div>
            )}
        </div>
    );
}
