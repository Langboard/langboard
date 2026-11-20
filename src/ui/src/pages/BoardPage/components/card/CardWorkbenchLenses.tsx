import CardNextAction from "@/pages/BoardPage/components/card/CardNextAction";
import BoardCardWorkState from "../board/BoardCardWorkState";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/core/models";
import type { CardDetailSection, IGetCardDetailsResponse } from "@/controllers/api/card/useGetCardDetails";
import { useEffect, useState } from "react";
import { formatDateTime, formatNumber } from "@/core/utils/LocaleFormat";

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
const ARTIFACT_LABELS = { pull_request: "Pull request artifact", run: "Run artifact", log: "Log artifact", url: "Link artifact" } as const;
export default function CardWorkbenchLenses({
    card,
    lens,
    receipts = [],
    agentExecution,
    checkingAgentExecution,
}: {
    card: ProjectCard.TModel;
    lens: CardWorkbenchLens;
    receipts?: IGetCardDetailsResponse["execution_receipts"];
    agentExecution?: IGetCardDetailsResponse["agent_execution"];
    checkingAgentExecution?: boolean;
}) {
    const [t, i18n] = useTranslation();
    const state = card.useField("work_state");
    const visibility = card.useField("visibility");
    const text = (key: string) => t(`card.lenses.${key}`);
    const labeled = (label: string, value: string | number) => t("common.Label and value", { label, value });
    const unknown = text("Unknown");
    const verification = state?.verification;
    const progress = state?.checklist_progress;
    const openAcceptance = progress
        ? `${formatNumber(progress.total - progress.completed, i18n.language)} / ${formatNumber(progress.total, i18n.language)}`
        : unknown;
    const visibleReceipts = lens === "history" ? receipts : receipts.filter((item) => item.generation === state?.execution_generation);
    return (
        <section className="min-w-0 space-y-3 break-words text-sm" aria-label={text(lens)}>
            <div role="status" className="rounded-md border p-3">
                {state ? <BoardCardWorkState state={state} /> : <p>{labeled(text("State"), unknown)}</p>}
            </div>
            {lens === "execution" && (
                <>
                    <CardNextAction card={card} editable />
                    <p>{text("Execution goal and scope unavailable")}</p>
                    <p>{labeled(text("Blockers"), state?.blocker_state ? text(state.blocker_state) : unknown)}</p>
                    {state?.dependency_state?.direct_blockers?.map((blocker) => (
                        <p key={blocker.relationship_uid}>{blocker.accessible ? (blocker.title ?? unknown) : text("Restricted prerequisite")}</p>
                    ))}
                    <p>{labeled(text("Open acceptance"), openAcceptance)}</p>
                    {checkingAgentExecution && visibility !== "PRIVATE" ? (
                        <p role="status">{labeled(text("Agent lifecycle"), t("common.Loading..."))}</p>
                    ) : (
                        <AgentExecutionSummary
                            execution={visibility !== "PRIVATE" && agentExecution?.generation === state?.execution_generation ? agentExecution : null}
                        />
                    )}
                    <p className="text-xs text-muted-foreground">{text("Responsibility note")}</p>
                </>
            )}
            {lens === "review" && (
                <>
                    <h3 className="font-semibold">
                        {labeled(text("Verification"), state?.verification_state ? text(state.verification_state) : unknown)}
                    </h3>
                    <p>{labeled(text("Source revision"), verification?.source_change_seq ?? unknown)}</p>
                    <p>
                        {labeled(
                            text("Environment"),
                            verification?.evidence.length ? [...new Set(verification.evidence.map((item) => item.environment))].join(", ") : unknown
                        )}
                    </p>
                    {verification?.evidence.map((item, index) => (
                        <div key={index} className="rounded-md border p-3">
                            <p>{labeled(item.checkitem_uid ?? text("Card evidence"), item.reference)}</p>
                            <p>
                                {item.source_revision} · {item.environment}
                            </p>
                        </div>
                    ))}
                    {verification?.required_checkitem_uids.map((uid) => (
                        <p key={uid}>
                            {labeled(uid, verification.evidence.some((item) => item.checkitem_uid === uid) ? text("Evidence recorded") : unknown)}
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
                    <p className="text-xs text-muted-foreground">
                        {Number.isFinite(Date.parse(created_at)) ? (
                            <time dateTime={created_at}>{formatDateTime(new Date(created_at), i18n.language)}</time>
                        ) : (
                            unknown
                        )}
                    </p>
                    {receipt.artifacts.map((artifact) => {
                        const label = Object.hasOwn(ARTIFACT_LABELS, artifact.type)
                            ? text(ARTIFACT_LABELS[artifact.type as keyof typeof ARTIFACT_LABELS])
                            : artifact.type;
                        return /^https?:\/\//i.test(artifact.url) ? (
                            <a className="block break-all underline" key={artifact.url} href={artifact.url} target="_blank" rel="noopener noreferrer">
                                {labeled(label, artifact.url)}
                            </a>
                        ) : (
                            <p key={artifact.url}>{labeled(label, artifact.url)}</p>
                        );
                    })}
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
    const labeled = (label: string, value: string | number) => t("common.Label and value", { label, value });
    const valid =
        execution?.schema_version === 1 &&
        execution.evidence_kind === "app_attestation" &&
        execution.current_authorization === "authorized" &&
        execution.process_liveness === "unknown" &&
        Number.isFinite(expiresAt) &&
        expiresAt > Date.now() &&
        Number.isFinite(Date.parse(execution.reported_at)) &&
        Number.isFinite(Date.parse(execution.evaluated_at));
    if (!valid) return <p>{labeled(text("Agent lifecycle"), text("Unknown"))}</p>;
    return (
        <section aria-label={text("Agent lifecycle")} className="space-y-1 rounded-md border p-3">
            <p className="font-medium">{labeled(text("App-reported start"), execution.app_key)}</p>
            <p>{labeled(text("Execution ID"), `${execution.start_uid} · #${execution.generation}`)}</p>
            <p>{text("Authorized at last check")}</p>
            <p>
                <time dateTime={execution.evaluated_at}>
                    {labeled(text("Checked at"), formatDateTime(new Date(execution.evaluated_at), i18n.language))}
                </time>
            </p>
            <p>
                <time dateTime={execution.authorization_expires_at}>
                    {labeled(text("Valid until"), formatDateTime(new Date(execution.authorization_expires_at), i18n.language))}
                </time>
            </p>
            <p>{labeled(text("Process liveness"), text("Unknown"))}</p>
        </section>
    );
}
