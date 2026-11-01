import CardNextAction from "@/pages/BoardPage/components/card/CardNextAction";
import BoardCardWorkState from "../board/BoardCardWorkState";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/core/models";
import type { CardDetailSection, IGetCardDetailsResponse } from "@/controllers/api/card/useGetCardDetails";
import { useEffect, useState } from "react";
import { formatDateTime } from "@/core/utils/LocaleFormat";

export type CardWorkbenchLens = "execution" | "review" | "history";
export const CARD_LENS_SECTIONS: Record<CardWorkbenchLens, CardDetailSection[]> = {
    execution: ["checklists", "execution_receipts", "agent_execution", "global_relationships", "project_columns", "project_labels", "bot_scopes"],
    review: ["checklists", "execution_receipts", "global_relationships", "project_columns", "project_labels", "bot_scopes"],
    history: [
        "description",
        "checklists",
        "attachments",
        "linked_wikis",
        "execution_receipts",
        "global_relationships",
        "project_columns",
        "project_labels",
        "bot_scopes",
    ],
};
export const CARD_BODY_SECTIONS = CARD_LENS_SECTIONS.history.filter((section) => section !== "execution_receipts");
export default function CardWorkbenchLenses({
    card,
    lens,
    receipts = [],
    agentExecution,
}: {
    card: ProjectCard.TModel;
    lens: CardWorkbenchLens;
    receipts?: IGetCardDetailsResponse["execution_receipts"];
    agentExecution?: IGetCardDetailsResponse["agent_execution"];
}) {
    const [t] = useTranslation();
    const state = card.useField("work_state");
    const visibility = card.useField("visibility");
    const text = (key: string) => t(`card.lenses.${key}`);
    const unknown = text("Unknown");
    const verification = state?.verification;
    const visibleReceipts = lens === "history" ? receipts : receipts.filter((item) => item.generation === state?.execution_generation);
    return (
        <section className="min-w-0 space-y-3 break-words text-sm" aria-label={text(lens)}>
            <div role="status" className="rounded-md border p-3">
                {state ? (
                    <BoardCardWorkState state={state} />
                ) : (
                    <p>
                        {text("State")}: {unknown}
                    </p>
                )}
            </div>
            {lens === "execution" && (
                <>
                    <CardNextAction card={card} />
                    <p>{text("Execution context unavailable")}</p>
                    <p>
                        {text("Blockers")}: {state?.blocker_state ? text(state.blocker_state) : unknown}
                    </p>
                    {state?.dependency_state?.direct_blockers?.map((blocker) => (
                        <p key={blocker.relationship_uid}>{blocker.accessible ? (blocker.title ?? unknown) : text("Restricted prerequisite")}</p>
                    ))}
                    <p>
                        {text("Open acceptance")}:{" "}
                        {state
                            ? `${state.checklist_progress.total - state.checklist_progress.completed} / ${state.checklist_progress.total}`
                            : unknown}
                    </p>
                    <AgentExecutionSummary
                        execution={visibility !== "PRIVATE" && agentExecution?.generation === state?.execution_generation ? agentExecution : null}
                    />
                    <p className="text-xs text-muted-foreground">{text("Responsibility note")}</p>
                </>
            )}
            {lens === "review" && (
                <>
                    <h3 className="font-semibold">
                        {text("Verification")}: {state?.verification_state ? text(state.verification_state) : unknown}
                    </h3>
                    <p>
                        {text("Source revision")}: {verification?.source_change_seq ?? unknown}
                    </p>
                    <p>
                        {text("Environment")}:{" "}
                        {verification?.evidence.length ? [...new Set(verification.evidence.map((item) => item.environment))].join(", ") : unknown}
                    </p>
                    {verification?.evidence.map((item, index) => (
                        <div key={index} className="rounded-md border p-3">
                            <p>
                                {item.checkitem_uid ?? text("Card evidence")}: {item.reference}
                            </p>
                            <p>
                                {item.source_revision} · {item.environment}
                            </p>
                        </div>
                    ))}
                    {verification?.required_checkitem_uids.map((uid) => (
                        <p key={uid}>
                            {uid}: {verification.evidence.some((item) => item.checkitem_uid === uid) ? text("Evidence recorded") : unknown}
                        </p>
                    ))}
                    <p className="text-xs text-muted-foreground">{text("Approval note")}</p>
                </>
            )}
            <h3 className="font-semibold">{text(lens === "history" ? "Past execution summaries" : "Current evidence")}</h3>
            {visibleReceipts.length === 0 && <p>{unknown}</p>}
            {visibleReceipts.map(({ generation, created_at, receipt, checklist_projection }) => (
                <div key={generation} className="space-y-1 rounded-md border p-3">
                    <p className="font-medium">
                        #{generation} · {receipt.status}
                    </p>
                    <p className="whitespace-pre-wrap">{receipt.summary}</p>
                    <p className="text-xs text-muted-foreground">{created_at}</p>
                    {receipt.artifacts.map((artifact) =>
                        /^https?:\/\//i.test(artifact.url) ? (
                            <a className="block break-all underline" key={artifact.url} href={artifact.url} target="_blank" rel="noopener noreferrer">
                                {artifact.type}: {artifact.url}
                            </a>
                        ) : (
                            <p key={artifact.url}>
                                {artifact.type}: {artifact.url}
                            </p>
                        )
                    )}
                    <ul aria-label={t("card.Execution evidence checklist")}>
                        {(checklist_projection ?? []).map((item) => (
                            <li key={item.item_uid}>
                                {item.is_checked ? "☑" : "☐"} {item.item_uid}: {item.kind} · {item.refs.join(", ")}
                            </li>
                        ))}
                    </ul>
                </div>
            ))}
        </section>
    );
}

function AgentExecutionSummary({ execution }: { execution: IGetCardDetailsResponse["agent_execution"] }) {
    const [t, i18n] = useTranslation();
    const [, refreshClock] = useState(0);
    const expiresAt = execution ? Date.parse(execution.authorization_expires_at) : NaN;
    useEffect(() => {
        if (!Number.isFinite(expiresAt)) return;
        const refresh = () => refreshClock((value) => value + 1);
        const timer = window.setTimeout(refresh, Math.max(0, Math.min(2_147_483_647, expiresAt - Date.now() + 1)));
        document.addEventListener("visibilitychange", refresh);
        return () => {
            window.clearTimeout(timer);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [expiresAt]);
    const text = (key: string) => t(`card.lenses.${key}`);
    const valid =
        execution?.schema_version === 1 &&
        execution.evidence_kind === "app_attestation" &&
        execution.current_authorization === "authorized" &&
        execution.process_liveness === "unknown" &&
        Number.isFinite(expiresAt) &&
        expiresAt > Date.now() &&
        Number.isFinite(Date.parse(execution.reported_at)) &&
        Number.isFinite(Date.parse(execution.evaluated_at));
    if (!valid)
        return (
            <p>
                {text("Agent lifecycle")}: {text("Unknown")}
            </p>
        );
    return (
        <section aria-label={text("Agent lifecycle")} className="space-y-1 rounded-md border p-3">
            <p className="font-medium">
                {text("App-reported start")}: {execution.app_key}
            </p>
            <p>
                {text("Execution ID")}: {execution.start_uid} · #{execution.generation}
            </p>
            <p>{text("Authorized at last check")}</p>
            <p>
                {text("Checked at")}: <time dateTime={execution.evaluated_at}>{formatDateTime(new Date(execution.evaluated_at), i18n.language)}</time>
            </p>
            <p>
                {text("Valid until")}:{" "}
                <time dateTime={execution.authorization_expires_at}>
                    {formatDateTime(new Date(execution.authorization_expires_at), i18n.language)}
                </time>
            </p>
            <p>
                {text("Process liveness")}: {text("Unknown")}
            </p>
        </section>
    );
}
