/** Decoration policy is independent of the slider's choice semantics. */
export const STEPPED_CHOICE_EFFECTS = {
    none: "stepped-choice-effect-none",
    emergency: "stepped-choice-effect-emergency",
    critical: "stepped-choice-effect-critical",
} as const;

export type TSteppedChoiceEffect = keyof typeof STEPPED_CHOICE_EFFECTS;
