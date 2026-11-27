/* eslint-disable @/max-len */
export interface IApiComfortTool {
    label: string;
    description: string;
    api_names: string[];
}

export const expandApiNamesWithComfortTools = (
    apiNames: string[] = [],
    comfortToolNames: string[] = [],
    comfortTools: Record<string, IApiComfortTool> = {}
) => {
    const callableApiNames: string[] = [];
    const seenApiNames = new Set<string>();
    const coveredApiNames = new Set<string>();

    const addApiName = (apiName: string) => {
        if (seenApiNames.has(apiName)) {
            return;
        }

        seenApiNames.add(apiName);
        callableApiNames.push(apiName);
    };

    comfortToolNames.forEach((comfortToolName) => {
        const comfortTool = comfortTools[comfortToolName];
        if (comfortTool) {
            comfortTool.api_names.forEach((apiName) => coveredApiNames.add(apiName));
            addApiName(comfortToolName);
        }
    });

    apiNames.forEach((apiName) => {
        if (!coveredApiNames.has(apiName)) {
            addApiName(apiName);
        }
    });

    return callableApiNames;
};

export const createApiComfortToolPrompt = (
    comfortToolNames: string[] = [],
    comfortToolDescriptions: Record<string, string> = {},
    comfortTools: Record<string, IApiComfortTool> = {}
) => {
    const promptLines = comfortToolNames.flatMap((comfortToolName) => {
        const comfortTool = comfortTools[comfortToolName];
        if (!comfortTool) {
            return [];
        }

        const lines = [
            `- ${comfortTool.label} (${comfortToolName})`,
            `  Base tools: ${comfortTool.api_names.join(", ")}`,
            `  Default behavior: ${comfortTool.description}`,
        ];

        const userDescription = comfortToolDescriptions[comfortToolName];
        if (userDescription) {
            lines.push(`  User description: ${userDescription}`);
        }

        return [lines.join("\n")];
    });

    if (!promptLines.length) {
        return "";
    }

    return [
        "Comfort tool instructions:",
        "The selected comfort tools are callable tools. When the task matches a comfort tool's behavior, call the comfort tool itself first instead of calling its base APIs one by one. The comfort tool executes its registered base APIs and returns the combined result. Only call base APIs separately when the comfort tool result is insufficient.",
        ...promptLines,
    ].join("\n");
};
