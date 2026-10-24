import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import Popover from "@/components/base/Popover";
import useGetGraphApprovals from "@/controllers/api/board/graphApprovals/useGetGraphApprovals";
import { EGraphApprovalScopeTable, EGraphApprovalStatus } from "@/core/models/GraphApprovalRequestModel";
import { getAuthStore } from "@/core/stores/AuthStore";
import { useBoardCard, useBoardCardPanel } from "@/core/providers/BoardCardProvider";
import { ProjectRole } from "@/core/models/roles";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDecisions } from "../../decision/DecisionContext";
import { DecisionStatus } from "../../decision/DecisionView";
import DecisionParticipantSelector, { ActorSelection } from "./DecisionParticipantSelector";

export default function BoardCardActionAddDecision({ buttonClassName }: { buttonClassName?: string }) {
    const { hasRoleAction } = useBoardCard();
    const { isActionPanelOpen } = useBoardCardPanel();
    if (!isActionPanelOpen || !hasRoleAction(ProjectRole.EAction.CardUpdate)) return null;
    return <DecisionAction buttonClassName={buttonClassName} />;
}

// Also used by the controlled native transport fixture.
export function DecisionAction({ buttonClassName }: { buttonClassName?: string }) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const { busy, pending } = useDecisions();
    return (
        <Popover.Root
            modal
            open={open}
            onOpenChange={(value) => {
                if (!busy && !pending) setOpen(value);
            }}
        >
            <Popover.Trigger asChild>
                <Button variant="secondary" className={buttonClassName}>
                    {t("card.decision.Add decision")}
                </Button>
            </Popover.Trigger>
            <Popover.Content align="end" className="max-h-[calc(100dvh-3rem)] w-[min(28rem,calc(100vw-2rem))] min-w-0 overflow-y-auto">
                {open && <DecisionComposer onClose={() => setOpen(false)} />}
            </Popover.Content>
        </Popover.Root>
    );
}
function DecisionComposer({ onClose }: { onClose: () => void }) {
    const [t] = useTranslation();
    const { projectUID, cardUID, page, locked, busy, pending, execute, reconfirm, reload } = useDecisions();
    const [sourceKind, setSourceKind] = useState("proposal");
    const [approvalUID, setApprovalUID] = useState("");
    const [options, setOptions] = useState([
        { key: "option-1", label: "" },
        { key: "option-2", label: "" },
    ]);
    const nextOption = useRef(3);
    const [pollQuorum, setPollQuorum] = useState(1);
    const [pollRules, setPollRules] = useState<Record<string, string>>({});
    const ruleChoices: Record<string, string[]> = {
        participation_policy: ["vote_only", "all_responses"],
        winner_policy: ["plurality", "majority_of_votes"],
        tie_policy: ["needs_info", "rejected"],
        no_quorum_policy: ["expired", "rejected"],
        no_winner_policy: ["needs_info", "rejected"],
        needs_info_policy: ["record", "block_result"],
        allow_change: ["true", "false"],
        tally_visibility: ["before", "after_vote", "after_close"],
    };
    const [question, setQuestion] = useState("");
    const [actorSelection, setActorSelection] = useState<ActorSelection>();
    const [selected, setSelected] = useState<{ uid: string; label: string }[]>([]);
    const [policy, setPolicy] = useState("");
    const [required, setRequired] = useState(1);
    const [deadline, setDeadline] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    useEffect(() => {
        setSelected(
            actorSelection?.preview.actor_uids.map((uid) => {
                const person = actorSelection.preview.actors.find((item) => item.uid === uid)!;
                return { uid, label: [person.firstname, person.lastname].filter(Boolean).join(" ") || person.username };
            }) ?? []
        );
        setConfirmed(false);
    }, [actorSelection?.preview]);
    const alive = useRef(true);
    const questionRef = useRef<HTMLTextAreaElement>(null);
    const focused = useRef(false);
    useEffect(() => {
        if (!locked && !focused.current) {
            questionRef.current?.focus();
            focused.current = true;
        }
    }, [locked]);
    useEffect(() => {
        alive.current = true;
        return () => {
            alive.current = false;
        };
    }, []);
    useEffect(() => {
        setConfirmed(false);
    }, [reconfirm]);
    const validPolicy =
        sourceKind !== "poll"
            ? !!policy && (policy !== "k_of_n" || (Number.isInteger(required) && required >= 1 && required <= selected.length))
            : !!deadline &&
              options.length >= 2 &&
              options.length <= 20 &&
              options.every((option) => !!option.label.trim()) &&
              new Set(options.map((option) => option.label.trim())).size === options.length &&
              Number.isInteger(pollQuorum) &&
              pollQuorum >= 1 &&
              pollQuorum <= selected.length &&
              Object.keys(ruleChoices).every((rule) => ruleChoices[rule].includes(pollRules[rule]));
    const valid =
        !!question.trim() &&
        validPolicy &&
        !!actorSelection?.confirmed &&
        selected.length > 0 &&
        selected.length <= 256 &&
        !!page?.target_revision &&
        (sourceKind !== "graph_approval" || !!approvalUID) &&
        (!deadline || (Number.isFinite(new Date(deadline).getTime()) && new Date(deadline).getTime() > Date.now()));
    return (
        <form
            className="min-w-0 space-y-3 break-words"
            aria-label={t("card.decision.New decision")}
            onSubmit={async (event) => {
                event.preventDefault();
                if (!valid || !confirmed || locked) return;
                const saved = await execute(`/board/${projectUID}/card/${cardUID}/decisions`, {
                    source_kind: sourceKind,
                    source_ref: cardUID,
                    ...(sourceKind === "graph_approval" ? { approval_uid: approvalUID } : {}),
                    question: question.trim(),
                    target_revision: page!.target_revision,
                    participant_uids: actorSelection!.preview.actor_uids,
                    actor_sources: actorSelection!.sources,
                    expected_actor_snapshot: actorSelection!.preview.snapshot,
                    ...(sourceKind !== "poll"
                        ? {
                              policy: {
                                  op: policy,
                                  required: policy === "k_of_n" ? required : 0,
                                  children: selected.map((person) => ({ actor_uid: person.uid })),
                              },
                          }
                        : {
                              poll: {
                                  ...pollRules,
                                  options: options.map((option) => ({ ...option, label: option.label.trim() })),
                                  participation_quorum: pollQuorum,
                                  allow_change: pollRules.allow_change === "true",
                              },
                          }),
                    deadline: deadline ? new Date(deadline).toISOString() : null,
                });
                if (saved && alive.current) onClose();
            }}
        >
            <h3 className="font-semibold">{t("card.decision.New decision")}</h3>
            <p>{t("card.decision.No execution")}</p>
            <DecisionStatus />
            <fieldset disabled={locked} className="min-w-0 space-y-3">
                <label className="block">
                    {t("card.decision.Decision kind")}
                    <select
                        aria-label={t("card.decision.Decision kind")}
                        className="w-full rounded border bg-background p-2"
                        value={sourceKind}
                        onChange={(event) => {
                            setSourceKind(event.target.value);
                            setApprovalUID("");
                            setSelected([]);
                            setActorSelection(undefined);
                            setConfirmed(false);
                        }}
                    >
                        <option value="proposal">{t("card.decision.Proposal")}</option>
                        <option value="poll">{t("card.decision.Named single choice poll")}</option>
                        <option value="graph_approval">{t("card.decision.HITL")}</option>
                    </select>
                </label>
                {sourceKind === "graph_approval" && (
                    <PendingApprovalSelector
                        onChange={(value) => {
                            setApprovalUID(value);
                            setConfirmed(false);
                        }}
                    />
                )}
                <label className="block">
                    {t("card.decision.Question")}
                    <textarea
                        ref={questionRef}
                        aria-label={t("card.decision.Question")}
                        maxLength={4000}
                        className="min-h-24 w-full rounded border bg-background p-2"
                        value={question}
                        onChange={(event) => {
                            setQuestion(event.target.value);
                            setConfirmed(false);
                        }}
                    />
                </label>
                <DecisionParticipantSelector
                    key={sourceKind}
                    sourceKind={sourceKind === "graph_approval" ? "proposal" : sourceKind}
                    onChange={setActorSelection}
                />
                <p>{t("card.decision.Selected count", { count: selected.length })}</p>
                <ol className="space-y-2">
                    {selected.map((person, index) => (
                        <li key={person.uid} className="flex flex-wrap items-center gap-2">
                            <span>
                                {index + 1}. {person.label}
                            </span>
                            <Button
                                size="sm"
                                type="button"
                                aria-label={`${t("card.decision.Move earlier")} ${person.label}`}
                                disabled={index === 0}
                                onClick={() => {
                                    const next = [...selected];
                                    [next[index - 1], next[index]] = [next[index], next[index - 1]];
                                    setSelected(next);
                                    setConfirmed(false);
                                }}
                            >
                                {t("card.decision.Move earlier")}
                            </Button>
                        </li>
                    ))}
                </ol>
                {sourceKind !== "poll" && (
                    <>
                        <label className="block">
                            {t("card.decision.Policy")}
                            <select
                                aria-label={t("card.decision.Policy")}
                                className="w-full rounded border bg-background p-2"
                                value={policy}
                                onChange={(event) => {
                                    setPolicy(event.target.value);
                                    setConfirmed(false);
                                }}
                            >
                                <option value="">{t("card.decision.Select policy")}</option>
                                {["all", "any", "k_of_n", "sequence"].map((value) => (
                                    <option key={value} value={value}>
                                        {t(`card.decision.${value}`)}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <p>{t("card.decision.Policy limits")}</p>
                        {policy === "sequence" && <p>{t("card.decision.Sequence order")}</p>}
                        {policy === "k_of_n" && (
                            <label className="block">
                                {t("card.decision.Required approvals")}
                                <Input
                                    type="number"
                                    min={1}
                                    max={selected.length}
                                    value={required}
                                    onChange={(event) => {
                                        setRequired(Number(event.target.value));
                                        setConfirmed(false);
                                    }}
                                />
                            </label>
                        )}
                    </>
                )}
                {sourceKind === "poll" && (
                    <div className="space-y-3">
                        <p>{t("card.decision.Poll limits")}</p>
                        {options.map((option, index) => (
                            <div key={option.key} className="space-y-1">
                                <label className="block">
                                    {t("card.decision.Option", { number: index + 1 })}
                                    <Input
                                        maxLength={500}
                                        value={option.label}
                                        onChange={(event) => {
                                            setOptions(
                                                options.map((item) => (item.key === option.key ? { ...item, label: event.target.value } : item))
                                            );
                                            setConfirmed(false);
                                        }}
                                    />
                                </label>
                                <Button
                                    type="button"
                                    size="sm"
                                    disabled={options.length <= 2}
                                    aria-label={`${t("card.decision.Remove option")} ${index + 1}`}
                                    onClick={() => {
                                        setOptions(options.filter((item) => item.key !== option.key));
                                        setConfirmed(false);
                                    }}
                                >
                                    {t("card.decision.Remove option")}
                                </Button>
                            </div>
                        ))}
                        <Button
                            type="button"
                            size="sm"
                            disabled={options.length >= 20}
                            onClick={() => {
                                setOptions([...options, { key: `option-${nextOption.current++}`, label: "" }]);
                                setConfirmed(false);
                            }}
                        >
                            {t("card.decision.Add option")}
                        </Button>
                        <label className="block">
                            {t("card.decision.Participation quorum")}
                            <Input
                                type="number"
                                min={1}
                                max={selected.length}
                                value={pollQuorum}
                                onChange={(event) => {
                                    setPollQuorum(Number(event.target.value));
                                    setConfirmed(false);
                                }}
                            />
                        </label>
                        {Object.entries(ruleChoices).map(([rule, choices]) => (
                            <label key={rule} className="block">
                                {t(`card.decision.${rule}`)}
                                <select
                                    aria-label={t(`card.decision.${rule}`)}
                                    className="w-full rounded border bg-background p-2"
                                    value={pollRules[rule] ?? ""}
                                    onChange={(event) => {
                                        setPollRules({ ...pollRules, [rule]: event.target.value });
                                        setConfirmed(false);
                                    }}
                                >
                                    <option value="">{t("card.decision.Select rule")}</option>
                                    {choices.map((value) => (
                                        <option key={value} value={value}>
                                            {t(`card.decision.rule_${value}`)}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        ))}
                    </div>
                )}
                <label className="block">
                    {t(sourceKind === "poll" ? "card.decision.Deadline required" : "card.decision.Deadline optional")}
                    <Input
                        type="datetime-local"
                        value={deadline}
                        onChange={(event) => {
                            setDeadline(event.target.value);
                            setConfirmed(false);
                        }}
                    />
                </label>
                <label className="flex items-start gap-2">
                    <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                    {t("card.decision.Confirm creation", { count: selected.length })}
                </label>
            </fieldset>
            <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" disabled={busy || !!pending} onClick={onClose}>
                    {t("card.decision.Cancel")}
                </Button>
                <Button type="submit" disabled={locked || !confirmed || !valid}>
                    {t("card.decision.Create")}
                </Button>
                <Button type="button" variant="outline" disabled={busy} onClick={reload}>
                    {t("card.decision.Refresh")}
                </Button>
            </div>
        </form>
    );
}

function PendingApprovalSelector({ onChange }: { onChange: (uid: string) => void }) {
    const [t] = useTranslation();
    const { projectUID, cardUID, reconfirm, reportError } = useDecisions();
    const [options, setOptions] = useState<{ uid: string; label: string }[]>([]);
    const [value, setValue] = useState("");
    const callback = useRef(onChange);
    callback.current = onChange;
    const { refetch } = useGetGraphApprovals(
        { project_uid: projectUID, scope_table: EGraphApprovalScopeTable.Card, scope_uid: cardUID, status: EGraphApprovalStatus.Pending, limit: 100 },
        { enabled: false, interceptToast: true }
    );
    useEffect(() => {
        let alive = true;
        const session = getAuthStore().getSessionVersion();
        setOptions([]);
        setValue("");
        callback.current("");
        void refetch().then((result) => {
            if (!alive) return;
            if (session !== getAuthStore().getSessionVersion() || result.isError) {
                reportError(result.error, session);
                return;
            }
            setOptions(
                (result.data?.approvals ?? [])
                    .filter(
                        (approval) =>
                            approval.project_uid === projectUID &&
                            approval.scope_table === EGraphApprovalScopeTable.Card &&
                            approval.scope_uid === cardUID &&
                            approval.status === EGraphApprovalStatus.Pending &&
                            approval.durable_run === true &&
                            approval.decision_bound === false
                    )
                    .map((approval) => ({
                        uid: approval.uid,
                        label: `${approval.tool_name ?? approval.api_name ?? approval.action_type} (${approval.uid})`,
                    }))
            );
        });
        return () => {
            alive = false;
        };
    }, [projectUID, cardUID, reconfirm, refetch]);
    return (
        <label className="block">
            {t("card.decision.Native approval")}
            <select
                aria-label={t("card.decision.Native approval")}
                className="w-full rounded border bg-background p-2"
                value={value}
                onChange={(event) => {
                    setValue(event.target.value);
                    callback.current(event.target.value);
                }}
            >
                <option value="">{t("card.decision.Select pending approval")}</option>
                {options.map((option) => (
                    <option key={option.uid} value={option.uid}>
                        {option.label}
                    </option>
                ))}
            </select>
            {!options.length && <span>{t("card.decision.No pending approvals")}</span>}
        </label>
    );
}
