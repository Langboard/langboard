import { GraphApprovalRequestModel } from "@/core/models";
import { Utils } from "@langboard/core/utils";

export function useGraphApprovalTitle(approval: GraphApprovalRequestModel.TModel, fallback = ""): string {
    const preview = approval.useField("preview_payload");
    const actionType = approval.useField("action_type");
    const toolName = approval.useField("tool_name");
    const apiName = approval.useField("api_name");
    const title = preview?.title;
    if (Utils.Type.isString(title) && title.trim()) {
        return title;
    }

    if (actionType) {
        return actionType;
    }

    if (toolName) {
        return toolName;
    }

    if (apiName) {
        return apiName;
    }

    return fallback;
}

export function useGraphApprovalSummary(approval: GraphApprovalRequestModel.TModel): string {
    const preview = approval.useField("preview_payload");
    const summary = preview?.summary;
    if (Utils.Type.isString(summary)) {
        return summary;
    }

    const description = preview?.description;
    if (Utils.Type.isString(description)) {
        return description;
    }

    return "";
}
