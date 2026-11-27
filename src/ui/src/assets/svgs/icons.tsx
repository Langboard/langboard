import DependsTheme from "@/assets/svgs/DependsTheme";
import AnthropicDark from "./icons/Anthropic-dark.svg?react";
import AnthropicLight from "./icons/Anthropic-light.svg?react";
import AWS from "./icons/AWS.svg?react";
import Azure from "./icons/Azure.svg?react";
import GoogleGenerativeAI from "./icons/GoogleGenerativeAI.svg?react";
import Groq from "./icons/Groq.svg?react";
import IBMWatsonDark from "./icons/IBMWatson-dark.svg?react";
import IBMWatsonLight from "./icons/IBMWatson-light.svg?react";
import Langflow from "./icons/Langflow.svg?react";
import LMStudio from "./icons/LMStudio.svg?react";
import Nvidia from "./icons/Nvidia.svg?react";
import OllamaDark from "./icons/Ollama-dark.svg?react";
import OllamaLight from "./icons/Ollama-light.svg?react";
import OpenAI from "./icons/OpenAI.svg?react";
import SambaNova from "./icons/SambaNova.svg?react";

export type TSVGIconMap = typeof svgIconMap;

const svgIconMap = {
    Anthropic: DependsTheme({ Dark: AnthropicDark, Light: AnthropicLight }),
    AWS,
    Azure,
    GoogleGenerativeAI,
    Groq,
    IBMWatson: DependsTheme({ Dark: IBMWatsonDark, Light: IBMWatsonLight }),
    Langflow,
    LMStudio,
    Nvidia,
    Ollama: DependsTheme({ Dark: OllamaDark, Light: OllamaLight }),
    OpenAI,
    SambaNova,
};

export default svgIconMap;
