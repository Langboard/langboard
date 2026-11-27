import { TAgentModelName } from "@/ai/constants";
import { TAmazonBedrockModelName, TAmazonBedrockRegion } from "@/ai/models/Amazon";
import { TAnthropicModelName } from "@/ai/models/Anthropic";
import { TAzureOpenAiApiVersion } from "@/ai/models/Azure";
import { TGoogleGenerativeAIModelName } from "@/ai/models/Google";
import { TGroqModelName } from "@/ai/models/Groq";
import { TNvidiaModelName } from "@/ai/models/Nvidia";
import { TIBMModelName, TIBMWatsonXURL } from "@/ai/models/IBM";
import { TOpenAiModelName } from "@/ai/models/OpenAI";
import { TSambaNovaModelName } from "@/ai/models/SambaNova";
import { TGetModelOptions } from "@/ai/models/types";

interface IBaseAgentInput {
    langModel: TAgentModelName;
}

export interface IAzureOpenAiAgentInput extends IBaseAgentInput {
    langModel: "Azure OpenAI";
    apiKey: string;
    apiVersion: TAzureOpenAiApiVersion;
    endpoint: string;
    deploymentName: string;
}

export interface IOpenAiAgentInput extends IBaseAgentInput {
    langModel: "OpenAI";
    model: TOpenAiModelName;
}

export interface IGroqAgentInput extends IBaseAgentInput {
    langModel: "Groq";
    apiKey?: string;
    model: TGroqModelName;
}

export interface IAnthropicAgentInput extends IBaseAgentInput {
    langModel: "Anthropic";
    apiKey?: string;
    model: TAnthropicModelName;
}

export interface IIBMWatsonAgentInput extends IBaseAgentInput {
    langModel: "IBM Watson";
    apiKey: string;
    model: TIBMModelName;
    projectId: string;
    url: TIBMWatsonXURL;
}

export interface INvidiaAgentInput extends IBaseAgentInput {
    langModel: "NVIDIA";
    apiKey?: string;
    model: TNvidiaModelName;
}

export interface IAmazonBedrockAgentInput extends IBaseAgentInput {
    langModel: "Amazon Bedrock";
    accessKeyID: string;
    secretAccessKey: string;
    sessionToken?: string;
    model: TAmazonBedrockModelName;
    region: TAmazonBedrockRegion;
}

export interface IGoogleGenerativeAiAgentInput extends IBaseAgentInput {
    langModel: "Google Generative AI";
    apiKey: string;
    model: TGoogleGenerativeAIModelName;
}

export interface ISambaNovaAgentInput extends IBaseAgentInput {
    langModel: "SambaNova";
    apiKey: string;
    model: TSambaNovaModelName;
}

export type TAgentInput =
    | IAzureOpenAiAgentInput
    | IOpenAiAgentInput
    | IGroqAgentInput
    | IAnthropicAgentInput
    | IIBMWatsonAgentInput
    | INvidiaAgentInput
    | IAmazonBedrockAgentInput
    | IGoogleGenerativeAiAgentInput
    | ISambaNovaAgentInput;

interface IBaseAgentFormInput {
    type: "text" | "password" | "select" | "integer";
    name: string;
    label: string;
    defaultValue?: string | number;
    nullable?: bool;
    checkDefault?: string;
}

export interface IStringAgentFormInput extends IBaseAgentFormInput {
    type: "text" | "password";
    defaultValue?: string;
    checkDefault?: string;
}

export interface ISelectAgentFormInput extends IBaseAgentFormInput {
    type: "select";
    defaultValue?: string;
    options: string[];
    getOptions?: (props: TGetModelOptions) => Promise<string[]>;
    checkDefault?: never;
}

export interface IIntegerAgentFormInput extends IBaseAgentFormInput {
    type: "integer";
    defaultValue?: number;
    min: number;
    max: number;
    checkDefault?: never;
}

export type TAgentFormInput = IStringAgentFormInput | ISelectAgentFormInput | IIntegerAgentFormInput;
