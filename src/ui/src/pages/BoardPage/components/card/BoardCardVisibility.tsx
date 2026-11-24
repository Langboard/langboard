import Button from "@/components/base/Button";
import Popover from "@/components/base/Popover";
import IconComponent from "@/components/base/IconComponent";
import { api } from "@/core/helpers/Api";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export default function BoardCardVisibility() {
    const { card, projectUID, canEditCard } = useBoardCard();
    const visibility = card.useField("visibility");
    const allowed = card.useField("can_change_visibility");
    const [t] = useTranslation();
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(false);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState(false);
    const [reviewedVisibility, setReviewedVisibility] = useState(visibility);
    if (!allowed || !canEditCard || (visibility !== "INTERNAL" && visibility !== "SHARED")) return null;
    const target = visibility === "INTERNAL" ? "SHARED" : "INTERNAL";
    const change = async () => {
        if (pending || visibility !== reviewedVisibility) return;
        setPending(true);
        setError(false);
        try {
            const response = await api.put(`/board/${projectUID}/card/${card.uid}/visibility`, {
                visibility: target,
                expected_visibility: reviewedVisibility,
                confirmed: true,
            });
            card.visibility = response.data.visibility;
            card.updated_at = response.data.updated_at;
            await queryClient.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] });
            await queryClient.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${card.uid}`] });
            setOpen(false);
        } catch {
            setError(true);
        } finally {
            setPending(false);
        }
    };
    return (
        <Popover.Root
            open={open}
            onOpenChange={(value) => {
                if (pending) return;
                setReviewedVisibility(visibility);
                setError(false);
                setOpen(value);
            }}
        >
            <Popover.Trigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="btn btn-ghost btn-xs h-7 gap-1 rounded-full px-2.5"
                    aria-label={t("card.Change card visibility")}
                >
                    <IconComponent icon={visibility === "SHARED" ? "globe" : "lock"} size="3" />
                    {t(visibility === "SHARED" ? "card.Public" : "card.Whisper")}
                </Button>
            </Popover.Trigger>
            <Popover.Content className="w-80 max-w-[calc(100vw-2rem)]" align="start">
                <p className="text-sm font-semibold">{t(target === "SHARED" ? "card.Make public" : "card.Make whisper")}</p>
                <p className="mt-2 text-sm text-muted-foreground">
                    {t(target === "SHARED" ? "card.Public visibility confirmation" : "card.Whisper visibility confirmation")}
                </p>
                {(error || visibility !== reviewedVisibility) && (
                    <p role="alert" className="mt-2 text-sm text-destructive">
                        {t("card.Visibility update failed")}
                    </p>
                )}
                <div className="mt-3 flex justify-end gap-2">
                    <Button type="button" variant="secondary" size="sm" disabled={pending} onClick={() => setOpen(false)}>
                        {t("common.Cancel")}
                    </Button>
                    <Button type="button" size="sm" disabled={pending || visibility !== reviewedVisibility} onClick={change}>
                        {t(pending ? "common.Updating..." : "common.Change")}
                    </Button>
                </div>
            </Popover.Content>
        </Popover.Root>
    );
}
