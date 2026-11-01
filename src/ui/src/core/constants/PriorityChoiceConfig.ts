import type { TFunction } from "i18next";
import type { TCardPriority } from "@langboard/core/constants";
import type { ISteppedChoice } from "@/components/SteppedChoiceSlider";
import type { TSteppedChoiceEffect } from "@/components/SteppedChoiceEffects";

export const PRIORITY_CHOICE_CONFIG: readonly { id: TCardPriority; color: string; icon: string; effect: TSteppedChoiceEffect }[] = [
    { id: "P3", color: "#9ca3af", icon: "leaf", effect: "none" },
    { id: "P2", color: "#a78bfa", icon: "flag", effect: "none" },
    { id: "P1", color: "#f97316", icon: "triangle-alert", effect: "emergency" },
    { id: "P0", color: "#ef4444", icon: "siren", effect: "critical" },
];

export const getPriorityChoice = (priority: TCardPriority) =>
    PRIORITY_CHOICE_CONFIG.find((choice) => choice.id === priority) ?? PRIORITY_CHOICE_CONFIG[1];

/** Resolve on render so a language change updates both the value and its hint. */
export const getPriorityChoices = (t: TFunction): ISteppedChoice<TCardPriority>[] =>
    PRIORITY_CHOICE_CONFIG.map((choice) => ({
        ...choice,
        label: t(`priority.levels.${choice.id}.label`),
        hint: t(`priority.levels.${choice.id}.hint`),
    }));
