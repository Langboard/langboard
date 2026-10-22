import BoardCardWorkState from "../board/BoardCardWorkState";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/core/models";
import type { CardDetailSection, IGetCardDetailsResponse } from "@/controllers/api/card/useGetCardDetails";

export type CardWorkbenchLens = "execution" | "review" | "history";
export const CARD_LENS_SECTIONS: Record<CardWorkbenchLens, CardDetailSection[]> = {
    execution: ["checklists", "execution_receipts", "global_relationships", "project_columns", "project_labels", "bot_scopes"],
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
}: {
    card: ProjectCard.TModel;
    lens: CardWorkbenchLens;
    receipts?: IGetCardDetailsResponse["execution_receipts"];
}) {
    const [t] = useTranslation();
    const state = card.useField("work_state");
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
                    <p>
                        {text("Agent lifecycle")}: {unknown}
                    </p>
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
