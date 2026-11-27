export const GOOGLE_GENERATIVE_AI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-preview-09-2025",
    "gemini-2.5-flash-lite-preview-09-2025",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-preview-image-generation",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-image-preview",
] as const;

export type TGoogleGenerativeAIModelName = (typeof GOOGLE_GENERATIVE_AI_MODELS)[number];
