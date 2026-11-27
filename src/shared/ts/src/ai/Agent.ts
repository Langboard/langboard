/* eslint-disable @typescript-eslint/no-explicit-any */
import { AMAZON_BEDROCK_MODELS, AMAZON_BEDROCK_REGIONS } from "@/ai/models/Amazon";
import { ANTHROPIC_MODELS } from "@/ai/models/Anthropic";
import { AZURE_OPEN_AI_API_VERSIONS } from "@/ai/models/Azure";
import { GOOGLE_GENERATIVE_AI_MODELS } from "@/ai/models/Google";
import { GROQ_MODELS } from "@/ai/models/Groq";
import { NVIDIA_MODELS } from "@/ai/models/Nvidia";
import { IBM_MODELS, IBM_WATSONX_URLS } from "@/ai/models/IBM";
import { OPEN_AI_MODELS } from "@/ai/models/OpenAI";
import { SAMBA_NOVA_MODELS } from "@/ai/models/SambaNova";
import { EBotPlatform, EBotPlatformRunningType, TAgentModelName } from "@/ai/constants";
import { TAgentFormInput } from "@/ai/form.types";
import { getAgentModelInputForm } from "@/ai/forms/AgentModelInput";
import { getN8NInputForm } from "@/ai/forms/N8NInput";

interface IBaseInputFormParams {
    platform: EBotPlatform;
    platformRunningType: EBotPlatformRunningType;
    model?: TAgentModelName;
    envs?: Record<string, any>;
}

interface IAgentModelInputFormParams extends IBaseInputFormParams {
    platform: EBotPlatform.Default;
    platformRunningType: EBotPlatformRunningType.Default;
    model: TAgentModelName;
}

export type TGetInputFormParams = IBaseInputFormParams | IAgentModelInputFormParams;

const getInputForm = ({ platform, platformRunningType, model, envs = {} }: TGetInputFormParams): TAgentFormInput[] => {
    switch (platform) {
        case EBotPlatform.Default:
            if (platformRunningType === EBotPlatformRunningType.Default && model) {
                return getAgentModelInputForm(model, envs);
            }
            return [];
        case EBotPlatform.N8N:
            return getN8NInputForm();
    }
    return [];
};

export const Agent = {
    AZURE_OPEN_AI_API_VERSIONS,
    OPEN_AI_MODELS,
    GROQ_MODELS,
    ANTHROPIC_MODELS,
    NVIDIA_MODELS,
    IBM_MODELS,
    IBM_WATSONX_URLS,
    AMAZON_BEDROCK_MODELS,
    AMAZON_BEDROCK_REGIONS,
    GOOGLE_GENERATIVE_AI_MODELS,
    SAMBA_NOVA_MODELS,
    getInputForm,
};
