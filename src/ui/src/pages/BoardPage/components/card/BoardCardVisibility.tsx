import SteppedChoiceSlider, { type ISteppedChoice } from "@/components/SteppedChoiceSlider";
import Button from "@/components/base/Button";
import Popover from "@/components/base/Popover";
import IconComponent from "@/components/base/IconComponent";
import { api } from "@/core/helpers/Api";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type Visibility = "PRIVATE" | "INTERNAL" | "SHARED";

export default function BoardCardVisibility() {
    const { card, projectUID, canEditCard } = useBoardCard();
    const visibility = card.useField("visibility");
    const allowed = card.useField("can_change_visibility");
    const allowedChoices = card.useField("visibility_choices") ?? [];
    const updatedAt = card.useField("updated_at");
    // Preserve the server timestamp: JavaScript Date truncates its microseconds.
    const version = card.useField("visibility_revision");
    const modifiedAt = updatedAt?.getTime();
    const [t] = useTranslation();
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(false);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState(false);
    const [reviewedVisibility, setReviewedVisibility] = useState(visibility);
    const [reviewedVersion, setReviewedVersion] = useState(version);
    const [reviewedModifiedAt, setReviewedModifiedAt] = useState(modifiedAt);
    const [target, setTarget] = useState<Visibility>(visibility ?? "INTERNAL");
    const choices: ISteppedChoice<Visibility>[] = [
        { id: "PRIVATE", label: t("card.Private"), hint: t("card.Private visibility guidance"), color: "hsl(var(--muted-foreground))" },
        { id: "INTERNAL", label: t("card.Whisper"), hint: t("card.Whisper visibility guidance"), color: "hsl(var(--primary))" },
        { id: "SHARED", label: t("card.Public"), hint: t("card.Public visibility guidance"), color: "hsl(var(--ring))" },
    ];
    const permittedChoices = choices.filter((choice) => allowedChoices.includes(choice.id));
    const stale = visibility !== reviewedVisibility || version !== reviewedVersion || modifiedAt !== reviewedModifiedAt;
    if (!allowed || !canEditCard || !visibility || !version || !allowedChoices.includes(visibility) || permittedChoices.length < 2) return null;
    const confirmationKey =
        target === "PRIVATE"
            ? "card.Private visibility confirmation"
            : target === "SHARED"
              ? "card.Public visibility confirmation"
              : "card.Whisper visibility confirmation";
    const titleKey = target === "PRIVATE" ? "card.Make private" : target === "SHARED" ? "card.Make public" : "card.Make whisper";
    const change = async () => {
        if (pending || stale || target === reviewedVisibility || !allowedChoices.includes(target)) return;
        setPending(true);
        setError(false);
        try {
            const response = await api.put(`/board/${projectUID}/card/${card.uid}/visibility`, {
                visibility: target,
                expected_visibility: reviewedVisibility,
                expected_updated_at: reviewedVersion,
                confirmed: true,
            });
            card.visibility = response.data.visibility;
            card.updated_at = response.data.updated_at;
            card.visibility_revision = response.data.updated_at;
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
                setReviewedVersion(version);
                setReviewedModifiedAt(modifiedAt);
                setTarget(visibility);
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
                    {t(visibility === "PRIVATE" ? "card.Private" : visibility === "SHARED" ? "card.Public" : "card.Whisper")}
                </Button>
            </Popover.Trigger>
            <Popover.Content className="w-80 max-w-[calc(100vw-2rem)]" align="start">
                <SteppedChoiceSlider
                    choices={permittedChoices}
                    value={target}
                    onChange={setTarget}
                    onCommit={setTarget}
                    disabled={pending || stale}
                    ariaLabel={t("card.Change card visibility")}
                />
                {target !== reviewedVisibility && (
                    <div className="mt-3 space-y-2">
                        <p className="text-sm font-semibold">{t(titleKey)}</p>
                        <p className="text-sm text-muted-foreground">{t(confirmationKey)}</p>
                    </div>
                )}
                {(error || stale) && (
                    <p role="alert" className="mt-2 text-sm text-destructive">
                        {t("card.Visibility update failed")}
                    </p>
                )}
                <div className="mt-3 flex justify-end gap-2">
                    <Button type="button" variant="secondary" size="sm" disabled={pending} onClick={() => setOpen(false)}>
                        {t("common.Cancel")}
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        disabled={pending || stale || target === reviewedVisibility || !allowedChoices.includes(target)}
                        onClick={change}
                    >
                        {t(pending ? "common.Updating..." : "common.Change")}
                    </Button>
                </div>
            </Popover.Content>
        </Popover.Root>
    );
}
