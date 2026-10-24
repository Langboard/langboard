import Button from "@/components/base/Button";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Decision, PollResponse, useDecisions } from "./DecisionContext";

export function DecisionStatus() {
    const [t] = useTranslation();
    const { message, pending, busy, retry, reload, stale } = useDecisions();
    return (
        <div className="space-y-2">
            <p role="status" aria-live="polite">
                {message}
            </p>
            {pending && (
                <>
                    <p>{t("card.decision.Retry exact")}</p>
                    <Button type="button" disabled={busy} onClick={retry}>
                        {t("card.decision.Retry")}
                    </Button>
                </>
            )}
            {(stale || pending) && (
                <Button type="button" disabled={busy} onClick={reload}>
                    {t("card.decision.Reload")}
                </Button>
            )}
        </div>
    );
}
export default function DecisionView({ decision }: { decision: Decision }) {
    const [t] = useTranslation();
    const { projectUID, locked, execute, reconfirm } = useDecisions();
    const [choice, setChoice] = useState("");
    const [optionUID, setOptionUID] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    useEffect(() => {
        setConfirmed(false);
        setChoice("");
        setOptionUID("");
    }, [decision.uid, decision.revision, reconfirm]);
    const poll = decision.source_kind === "poll" ? decision.poll : undefined;
    const hitl = decision.source_kind === "graph_approval";
    const canRespond = decision.allowed_actions.includes("respond");
    return (
        <article aria-label={decision.question} className="min-w-0 space-y-3 break-words rounded border p-3">
            <h3 className="whitespace-pre-wrap font-semibold">{decision.question}</h3>
            <p>
                {t(hitl ? "card.decision.HITL policy state" : "card.decision.State")}:{" "}
                {t(`card.decision.${decision.state}`, { defaultValue: decision.state })}
            </p>
            {hitl && (
                <div className="space-y-1">
                    <p>{t("card.decision.HITL")}</p>
                    <p>{t("card.decision.HITL manual resume")}</p>
                    {decision.state === "passed" && <p>{t("card.decision.HITL passed")}</p>}
                </div>
            )}
            {decision.blocked_reason && (
                <p>
                    {t("card.decision.Reason")}: {t(`card.decision.${decision.blocked_reason}`, { defaultValue: decision.blocked_reason })}
                </p>
            )}
            <p>
                {t("card.decision.Deadline")}: {decision.deadline ? new Date(decision.deadline).toLocaleString() : t("card.decision.No deadline")}
            </p>
            <p>
                {t("card.decision.Execution result")}:{" "}
                {decision.effect_result === null ? t("card.decision.No execution") : JSON.stringify(decision.effect_result)}
            </p>
            <details>
                <summary>{t("card.decision.Original source and policy")}</summary>
                <dl className="space-y-1 break-all text-sm">
                    <dt>{t("card.decision.Source")}</dt>
                    <dd>
                        {decision.source_kind}: {decision.source_ref}
                    </dd>
                    {hitl && (
                        <>
                            <dt>{t("card.decision.Native approval")}</dt>
                            <dd>{decision.approval_uid ?? t("card.decision.Unavailable")}</dd>
                        </>
                    )}
                    <dt>{t("card.decision.Target revision")}</dt>
                    <dd>{decision.target_revision}</dd>
                    <dt>{t("card.decision.Policy revision")}</dt>
                    <dd>{decision.policy_revision}</dd>
                    <dt>{t("card.decision.Round")}</dt>
                    <dd>{decision.round_id}</dd>
                    <dt>{t("card.decision.Participants")}</dt>
                    <dd>{decision.participant_uids.join(", ")}</dd>
                    <dt>{t("card.decision.Policy")}</dt>
                    <dd>
                        <pre className="whitespace-pre-wrap break-all">{JSON.stringify(decision.policy, null, 2)}</pre>
                    </dd>
                </dl>
            </details>
            {poll && <PollDetails poll={poll} />}
            {canRespond && (
                <form
                    className="space-y-2"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (!confirmed || !choice || locked) return;
                        void execute(`/board/${projectUID}/decisions/${decision.uid}/responses`, {
                            round_id: decision.round_id,
                            expected_revision: decision.revision,
                            target_revision: decision.target_revision,
                            policy_revision: decision.policy_revision,
                            choice,
                            ...(poll && choice === "vote" ? { option_uid: optionUID } : {}),
                        });
                    }}
                >
                    <fieldset disabled={locked} className="space-y-2">
                        <legend>{t("card.decision.Response")}</legend>
                        {poll?.options.map((option) => (
                            <label key={option.uid} className="flex items-start gap-2">
                                <input
                                    type="radio"
                                    name={`response-${decision.uid}`}
                                    value={option.uid}
                                    checked={choice === "vote" && optionUID === option.uid}
                                    onChange={() => {
                                        setChoice("vote");
                                        setOptionUID(option.uid);
                                        setConfirmed(false);
                                    }}
                                />
                                {option.label}
                            </label>
                        ))}
                        {(poll ? ["abstain", "needs_info"] : ["approve", "reject", "abstain", "needs_info"]).map((value) => (
                            <label key={value} className="flex items-start gap-2">
                                <input
                                    type="radio"
                                    name={`response-${decision.uid}`}
                                    value={value}
                                    checked={choice === value}
                                    onChange={() => {
                                        setChoice(value);
                                        setConfirmed(false);
                                    }}
                                />
                                {t(`card.decision.${value}`)}
                            </label>
                        ))}
                        <label className="flex items-start gap-2">
                            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                            {t("card.decision.Confirm response")}
                        </label>
                    </fieldset>
                    <Button type="submit" disabled={locked || !choice || !confirmed}>
                        {t(poll?.my_response && poll.can_change ? "card.decision.Change response" : "card.decision.Submit response")}
                    </Button>
                </form>
            )}
        </article>
    );
}

function PollDetails({ poll }: { poll: NonNullable<Decision["poll"]> }) {
    const [t] = useTranslation();
    const choiceLabel = (response: PollResponse) =>
        response.choice === "vote"
            ? (poll.options.find((option) => option.uid === response.option_uid)?.label ?? response.option_uid)
            : t(`card.decision.${response.choice}`);
    const ballot = (response: PollResponse) => (
        <div className="space-y-1 break-all">
            <p>
                {response.actor_uid}: {choiceLabel(response)}
            </p>
            <p>
                {t("card.decision.Recorded revision", { revision: response.response_revision })} · {new Date(response.responded_at).toLocaleString()}
            </p>
        </div>
    );
    return (
        <div className="space-y-2">
            <p>
                {t("card.decision.Poll phase")}: {t(`card.decision.${poll.phase}`)}
            </p>
            {poll.outcome_reason && (
                <p>
                    {t("card.decision.Reason")}: {t(`card.decision.${poll.outcome_reason}`, { defaultValue: poll.outcome_reason })}
                </p>
            )}
            {poll.tally_visible && poll.winner_option_uid && (
                <p>
                    {t("card.decision.Winner")}:{" "}
                    {poll.options.find((option) => option.uid === poll.winner_option_uid)?.label ?? poll.winner_option_uid}
                </p>
            )}
            {poll.my_response && (
                <div>
                    <h4 className="font-semibold">{t("card.decision.My recorded response")}</h4>
                    {ballot(poll.my_response)}
                </div>
            )}
            {poll.tally_visible && poll.tally ? (
                <div className="space-y-2">
                    <p>{t("card.decision.Participation count", { count: poll.tally.participation_count })}</p>
                    <p>{t("card.decision.Option vote count", { count: poll.tally.vote_count })}</p>
                    <p>{t("card.decision.Abstention count", { count: poll.tally.abstain_count })}</p>
                    <p>{t("card.decision.Needs info count", { count: poll.tally.needs_info_count })}</p>
                    <ul>
                        {poll.options.map((option) => (
                            <li key={option.uid}>
                                {option.label}: {poll.tally!.counts_by_option[option.uid] ?? t("card.decision.Unavailable")}
                            </li>
                        ))}
                    </ul>
                    {poll.responses && (
                        <details>
                            <summary>{t("card.decision.Named ballots")}</summary>
                            <ul className="space-y-2">
                                {poll.responses.map((response) => (
                                    <li key={response.actor_uid}>{ballot(response)}</li>
                                ))}
                            </ul>
                        </details>
                    )}
                </div>
            ) : (
                <p>{t("card.decision.Tally hidden")}</p>
            )}
            <details>
                <summary>{t("card.decision.Poll rules")}</summary>
                <dl className="space-y-1">
                    {Object.entries(poll.settings).map(([rule, value]) => (
                        <div key={rule}>
                            <dt className="font-semibold">{t(`card.decision.${rule}`)}</dt>
                            <dd>{typeof value === "number" ? value : t(`card.decision.rule_${String(value)}`)}</dd>
                        </div>
                    ))}
                </dl>
            </details>
        </div>
    );
}
