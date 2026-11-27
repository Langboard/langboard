/* eslint-disable @typescript-eslint/no-explicit-any */
import { createOneTimeToken } from "@/core/ai/BotOneTimeToken";
import BaseRequest, { IRequestData, IRequestExecuteParams, IRequestParams } from "@/core/ai/requests/BaseRequest";
import { LangboardCalledVariablesComponent } from "@/core/ai/helpers/TweaksComponent";
import SnowflakeID from "@/core/db/SnowflakeID";
import { api } from "@/core/helpers/Api";
import { EHttpStatus } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import formidable from "formidable";
import fs from "fs";
import BaseStreamResponse from "@/core/ai/responses/BaseStreamResponse";
import { IBotRequestModel } from "@/core/ai/types";
import { N8NStreamResponse, parseN8NResponse } from "@/core/ai/responses/N8NResponse";
import { EAgentPermissionLevel, EBotPlatformRunningType } from "@langboard/core/ai";

class N8NRequest extends BaseRequest {
    protected createRequestData({ requestModel, headers, useStream }: IRequestExecuteParams & { headers: Record<string, any> }): IRequestData | null {
        const apiPermissionLevel = requestModel.restData?.api_permission_level ?? EAgentPermissionLevel.Read;
        const oneTimeToken = createOneTimeToken(new SnowflakeID(requestModel.userId), apiPermissionLevel);

        const queryParams = new URLSearchParams({
            stream: useStream ? "true" : "false",
        });

        let url;
        switch (this.internalBot.platform_running_type) {
            case EBotPlatformRunningType.Default:
                url = this.baseURL;
                break;
            default:
                return null;
        }

        url = `${url}?${queryParams.toString()}`;

        requestModel.tweaks = requestModel.tweaks ?? {};

        const component = new LangboardCalledVariablesComponent(
            "chat",
            oneTimeToken,
            "user",
            { uid: new SnowflakeID(requestModel.userId).toShortCode() },
            requestModel.projectUID,
            requestModel.restData
        );

        let reqData: IRequestData["reqData"] = {
            input_value: requestModel.message,
            run_type: "internal_bot",
            uid: this.internalBot.uid,
            tweaks: {
                ...requestModel.tweaks,
                ...component.toTweaks(),
            },
        };

        const settings: Record<string, any> = {
            input_key: "input_value",
            output_key: "message",
            file_key: "file",
        };

        if (this.internalBot.platform_running_type === EBotPlatformRunningType.Default) {
            reqData = this.#setRequestData(requestModel, headers, reqData, settings);
        }

        return {
            url,
            oneTimeToken,
            reqData,
            settings,
        };
    }

    protected createStreamResponse({ requestModel, headers, task }: Omit<IRequestParams, "useStream">): BaseStreamResponse {
        const [abortController, finish] = task ?? [undefined, undefined];

        return new N8NStreamResponse({
            url: requestModel.url,
            headers: headers,
            body: requestModel.reqData,
            signal: abortController?.signal,
            onEnd: finish,
            settings: requestModel.settings,
        });
    }

    protected convertResponse(data: Record<string, any>, settings?: Record<string, any>): string {
        return parseN8NResponse(data, settings) ?? "";
    }

    public async upload(file: formidable.File): Promise<string | null> {
        return file.filepath;
    }

    public async isAvailable(): Promise<bool> {
        try {
            const healthCheck = await api.get(`${this.baseURL}/healthz`, {
                headers: this.getBotRequestHeaders(),
            });

            return healthCheck.status === EHttpStatus.HTTP_200_OK;
        } catch {
            return false;
        }
    }

    #setRequestData(
        requestModel: IBotRequestModel,
        headers: Record<string, string>,
        reqData: Record<string, any>,
        settings: Record<string, any>
    ): IRequestData["reqData"] {
        let result: IRequestData["reqData"] = { ...reqData };
        try {
            const botValue: Record<string, any> = Utils.Json.Parse(this.internalBot.value ?? "{}");

            if (botValue.input_key) {
                result.tweaks[botValue.input_key] = requestModel.message;
                settings.input_key = botValue.input_key;
                delete result.tweaks.input_value;
            }

            if (botValue.output_key) {
                settings.output_key = botValue.output_key;
            }

            if (requestModel.filePath) {
                const formData = new FormData();
                Object.entries(reqData).forEach(([key, value]) => {
                    if (Utils.Type.isString(value) || Utils.Type.isNumber(value) || Utils.Type.isBool(value)) {
                        formData.append(key, String(value));
                        return;
                    }

                    formData.append(key, Utils.Json.Stringify(value));
                });

                formData.append(botValue.file_key ?? "file", fs.createReadStream(requestModel.filePath));
                result = formData;

                settings.file_key = botValue.file_key ?? "file";
                headers["Content-Type"] = "multipart/form-data";
            }
        } catch {
            // Ignore parsing errors
        }

        return result;
    }
}

export default N8NRequest;
