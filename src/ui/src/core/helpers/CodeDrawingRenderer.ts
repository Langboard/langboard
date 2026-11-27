import { renderCodeDrawing } from "@platejs/code-drawing";
import type { CodeDrawingType } from "@platejs/code-drawing";

const PLACEHOLDER_CODE_PATTERN = /^(plant_uml_code|graphviz_code|flowchart_code|mermaid_code|math_code)$/i;
const FLOWCHART_NODE_DEFINITION_PATTERN = /^\s*([A-Za-z][\w-]*)\s*(?:\([^)]*\))?\s*=>/;
const FLOWCHART_CONNECTION_TOKEN_PATTERN = /^\s*([A-Za-z][\w-]*)/;

const getFlowchartDefinitionIDs = (code: string) => {
    const definitionIDs = new Set<string>();

    for (const line of code.split("\n")) {
        const match = line.match(FLOWCHART_NODE_DEFINITION_PATTERN);
        if (match) {
            definitionIDs.add(match[1]);
        }
    }

    return definitionIDs;
};

const getFlowchartConnectionIDs = (code: string) => {
    const connectionIDs: string[] = [];

    for (const line of code.split("\n")) {
        if (!line.includes("->") || line.includes("=>")) {
            continue;
        }

        for (const token of line.split("->")) {
            const match = token.match(FLOWCHART_CONNECTION_TOKEN_PATTERN);
            if (match) {
                connectionIDs.push(match[1]);
            }
        }
    }

    return connectionIDs;
};

const canRenderFlowchart = (code: string) => {
    const definitionIDs = getFlowchartDefinitionIDs(code);
    const connectionIDs = getFlowchartConnectionIDs(code);

    return definitionIDs.size > 0 && connectionIDs.length > 0 && connectionIDs.every((id) => definitionIDs.has(id));
};

export const renderDiagram = (drawingType: CodeDrawingType, code: string) => {
    if (drawingType === "Flowchart" && !canRenderFlowchart(code)) {
        throw new Error("Invalid Flowchart syntax.");
    }

    return renderCodeDrawing(drawingType, code);
};

export const canRenderDiagram = (drawingType: CodeDrawingType, code: string) => {
    const trimmedCode = code.trim();
    if (!trimmedCode || PLACEHOLDER_CODE_PATTERN.test(trimmedCode)) {
        return false;
    }

    switch (drawingType) {
        case "PlantUml":
            return /^@startuml\b/i.test(trimmedCode) && /@enduml\s*$/i.test(trimmedCode);
        case "Graphviz":
            return /^(strict\s+)?(di)?graph\b/i.test(trimmedCode);
        case "Flowchart":
            return canRenderFlowchart(trimmedCode);
        case "Mermaid":
            return /^(sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|flowchart|graph)\b/i.test(trimmedCode);
        default:
            return false;
    }
};
