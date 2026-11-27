/* eslint-disable @typescript-eslint/no-explicit-any */
import BaseStreamResponse from "@/core/ai/responses/BaseStreamResponse";

export class GraphStreamResponse extends BaseStreamResponse {
    public parseResponseChunk(chunk: Record<string, any>): string | { end?: true; error?: any; interrupt?: Record<string, any> } | undefined {
        if (!chunk.event || !chunk.data) {
            return undefined;
        }

        const { event, data } = chunk;

        switch (event) {
            case "token":
                return data.chunk;
            case "interrupt":
                return { interrupt: data };
            case "error":
                return { error: data.error ?? data };
            case "end":
                return { end: true };
        }

        return undefined;
    }
}
