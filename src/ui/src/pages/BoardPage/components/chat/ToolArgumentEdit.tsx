import { IGraphArgumentEdit, IGraphExecutionPlan } from "@/core/models/GraphApprovalRequestModel";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/** Business arguments only. Submission never constitutes execution approval. */
export default function ToolArgumentEdit({
    plan,
    disabled,
    onSubmit,
}: {
    plan: IGraphExecutionPlan;
    disabled?: boolean;
    onSubmit: (edit: IGraphArgumentEdit) => void;
}) {
    const [t] = useTranslation();
    const [argumentsJSON, setArgumentsJSON] = useState(() => JSON.stringify(plan.arguments.tool_args, null, 2));
    const [invalid, setInvalid] = useState(false);
    const submit = () => {
        try {
            const args = JSON.parse(argumentsJSON);
            if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error();
            setInvalid(false);
            onSubmit({ plan_id: plan.plan_id, args_hash: plan.args_hash, tool_args: args });
        } catch {
            setInvalid(true);
        }
    };
    return (
        <details className="mt-2">
            <summary className="cursor-pointer text-sm">{t("bot.Edit tool arguments")}</summary>
            <p className="my-2 text-xs">{t("bot.Edits require a new approval")}</p>
            <textarea
                className="textarea textarea-sm w-full border p-2 font-mono"
                rows={6}
                aria-label={t("bot.Tool arguments JSON")}
                value={argumentsJSON}
                disabled={disabled}
                onChange={(event) => setArgumentsJSON(event.target.value)}
            />
            {invalid && (
                <p role="alert" className="text-error">
                    {t("bot.Enter a valid JSON object")}
                </p>
            )}
            <button type="button" className="btn btn-sm mt-2" disabled={disabled} onClick={submit}>
                {t("bot.Submit edit for approval")}
            </button>
        </details>
    );
}
