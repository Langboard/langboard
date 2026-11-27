/* eslint-disable @typescript-eslint/no-explicit-any */
import { API_URL } from "@/Constants";
import { DATA_TEXT_FORMAT_DESCRIPTIONS } from "@/core/utils/EditorUtils";

abstract class BaseTweaksComponent {
    public abstract toTweaks(): Record<string, any>;
    public abstract toData(): Record<string, any>;
}

export class LangboardCalledVariablesComponent extends BaseTweaksComponent {
    #model: Record<string, any>;

    constructor(
        event: string,
        app_api_token: string,
        current_runner_type: "bot" | "user",
        current_runner_data?: Record<string, any>,
        project_uid?: string,
        rest_data?: Record<string, any>,
        custom_markdown_formats: Record<string, string> = DATA_TEXT_FORMAT_DESCRIPTIONS
    ) {
        super();
        this.#model = {
            base_url: API_URL,
            event,
            app_api_token,
            current_runner_type,
            current_runner_data,
            project_uid,
            rest_data,
            custom_markdown_formats,
        };
    }

    public toTweaks(): Record<string, any> {
        return { LangboardCalledVariablesComponent: this.toData() };
    }

    public toData(): Record<string, any> {
        return { ...this.#model };
    }
}

export class LangboardCalledAPIToolsComponent extends BaseTweaksComponent {
    #api_names: string[];

    constructor(api_names: string[]) {
        super();
        this.#api_names = api_names;
    }

    public toTweaks(): Record<string, any> {
        return { LangboardCalledAPIToolsComponent: this.toData() };
    }

    public toData(): Record<string, any> {
        return { base_url: API_URL, api_names: this.#api_names };
    }
}

export class LangboardFile extends BaseTweaksComponent {
    #filePath: string;

    constructor(filePath: string) {
        super();
        this.#filePath = filePath;
    }

    public toTweaks(): Record<string, any> {
        return { LangboardFile: this.toData() };
    }

    public toData(): Record<string, any> {
        return { path: this.#filePath };
    }
}
