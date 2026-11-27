/* eslint-disable @typescript-eslint/no-explicit-any */
import { AI_REQUEST_TIMEOUT, AI_REQUEST_TRIALS } from "@/Constants";
import BaseStreamResponse from "@/core/ai/responses/BaseStreamResponse";
import { IBotRequestModel } from "@/core/ai/types";
import { api } from "@/core/helpers/Api";
import Logger from "@/core/utils/Logger";
import InternalBot from "@/models/InternalBot";
import { IProjectAssignedInternalBotSettings } from "@/models/ProjectAssignedInternalBot";
import { EBotPlatform } from "@langboard/core/ai";
import formidable from "formidable";

export interface IRequestExecuteParams {
    requestModel: IBotRequestModel;
    task?: [AbortController, () => void];
    useStream?: bool;
}

export interface IRequestParams {
    requestModel: IRequestData;
    headers: Record<string, any>;
    task?: [AbortController, () => void];
    useStream: bool;
    retried?: number;
}

export interface IRequestData {
    url: string;
    oneTimeToken: string;
    reqData: Record<string, any> | FormData;
    settings?: Record<string, any>;
}

abstract class BaseRequest {
    constructor(
        protected internalBot: InternalBot,
        protected baseURL: string,
        protected internalBotSettings?: IProjectAssignedInternalBotSettings
    ) {
        this.baseURL = !baseURL.endsWith("/") ? baseURL : baseURL.slice(0, -1);
    }

    public async execute({ requestModel, task, useStream = false }: IRequestExecuteParams) {
        const headers = this.getBotRequestHeaders();

        const apiRequestModel = this.createRequestData({
            requestModel,
            headers,
            useStream,
        });

        if (!apiRequestModel) {
            return null;
        }

        return await this.request({ requestModel: apiRequestModel, headers, task, useStream });
    }

    protected abstract createRequestData(params: IRequestExecuteParams & { headers: Record<string, any> }): IRequestData | null;
    protected abstract createStreamResponse(params: Omit<IRequestParams, "useStream">): BaseStreamResponse;
    protected abstract convertResponse(data: Record<string, any>, settings?: Record<string, any>): string | null;
    public abstract upload(file: formidable.File): Promise<string | null>;
    public abstract isAvailable(): Promise<bool>;

    protected async request({ requestModel, headers, task, useStream, retried = 0 }: IRequestParams): Promise<string | null | BaseStreamResponse> {
        if (useStream) {
            return this.createStreamResponse({ requestModel, headers, task, retried });
        }

        const [abortController, finish] = task ?? [undefined, undefined];
        let result = null;
        try {
            const response = await api.post(requestModel.url, requestModel.reqData, {
                headers,
                timeout: AI_REQUEST_TIMEOUT * 1000,
                signal: abortController?.signal,
            });

            if (response.status !== 200) {
                throw new Error("Bot request failed");
            }

            result = this.convertResponse(response.data, requestModel.settings);
        } catch (error) {
            if (retried < AI_REQUEST_TRIALS) {
                return await this.request({ requestModel, headers, task, useStream, retried: retried + 1 });
            }

            Logger.error(error, "\n");
        }

        finish?.();

        return result;
    }

    protected getBotRequestHeaders() {
        const headers: Record<string, any> = {
            Accept: "application/json",
            "Content-Type": "application/json",
        };

        switch (this.internalBot.platform) {
            case EBotPlatform.Default:
                break;
            case EBotPlatform.Langflow:
                headers["X-API-KEY"] = this.internalBot.api_key;
                break;
            case EBotPlatform.N8N:
                headers["Authorization"] = this.internalBot.api_key;
                break;
            default:
                throw new Error(`Unsupported platform: ${this.internalBot.platform}`);
        }

        return headers;
    }

    protected getTitlePrompt() {
        return `You are an expert at creating concise and relevant titles based on user input.
Generate a title that accurately reflects the content and purpose of the input provided by the user.
Keep the title brief, engaging, and to the point.
YOU MUST RESPOND WITH ONLY THE TITLE AND NO ADDITIONAL TEXT.`;
    }
}

export default BaseRequest;
