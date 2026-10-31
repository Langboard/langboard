import type { TFunction } from "i18next";
import type { TCardPriority } from "@langboard/core/constants";
import type { ISteppedChoice } from "@/components/SteppedChoiceSlider";
import type { TSteppedChoiceEffect } from "@/components/SteppedChoiceEffects";

export const PRIORITY_CHOICE_CONFIG: readonly { id: TCardPriority; color: string; effect: TSteppedChoiceEffect }[] = [
    { id: "P3", color: "#9ca3af", effect: "none" },
    { id: "P2", color: "#a78bfa", effect: "none" },
    { id: "P1", color: "#f97316", effect: "emergency" },
    { id: "P0", color: "#ef4444", effect: "critical" },
];

/** Resolve on render so a language change updates both the value and its hint. */
export const getPriorityChoices = (t: TFunction): ISteppedChoice<TCardPriority>[] =>
    PRIORITY_CHOICE_CONFIG.map((choice) => ({
        ...choice,
        label: t(`priority.levels.${choice.id}.label`),
        hint: t(`priority.levels.${choice.id}.hint`),
    }));
