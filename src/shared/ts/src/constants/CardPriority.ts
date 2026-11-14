export const CARD_PRIORITY_VALUES = ["P0", "P1", "P2", "P3"] as const;
export type TCardPriority = (typeof CARD_PRIORITY_VALUES)[number];
export const DEFAULT_CARD_PRIORITY: TCardPriority = "P2";
