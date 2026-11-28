import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TCardPriority } from "@langboard/core/constants";
import SteppedChoiceSlider from "@/components/SteppedChoiceSlider";
import { getPriorityChoices } from "@/core/constants/PriorityChoiceConfig";

export default function CardPriorityField({
    value,
    disabled,
    save,
    effectsEnabled = false,
}: {
    value: TCardPriority;
    disabled: boolean;
    save: (value: TCardPriority) => Promise<unknown>;
    effectsEnabled?: boolean;
}) {
    const [t] = useTranslation();
    // The enclosing card owns motion unless an open popover explicitly takes over decoration.
    const choices = useMemo(
        () => getPriorityChoices(t).map((choice) => (effectsEnabled ? choice : { ...choice, effect: "none" as const })),
        [t, effectsEnabled]
    );
    const [preview, setPreview] = useState(value);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const pendingRef = useRef(false);
    const confirmedRef = useRef(value);
    useEffect(() => {
        confirmedRef.current = value;
        setPreview(value);
    }, [value]);
    const commit = async (next: TCardPriority) => {
        if (disabled || pendingRef.current || next === confirmedRef.current) return;
        pendingRef.current = true;
        setPending(true);
        setFailed(false);
        try {
            await save(next);
        } catch {
            setFailed(true);
        } finally {
            setPreview(confirmedRef.current);
            pendingRef.current = false;
            setPending(false);
        }
    };
    return (
        <div className="min-w-0 space-y-2" aria-busy={pending}>
            <SteppedChoiceSlider
                choices={choices}
                value={preview}
                onChange={setPreview}
                onCommit={(next) => void commit(next)}
                disabled={disabled || pending}
                ariaLabel={t("priority.label")}
            />
            {failed && (
                <span role="alert" className="text-xs text-destructive">
                    {t("priority.saveError")}
                </span>
            )}
        </div>
    );
}
