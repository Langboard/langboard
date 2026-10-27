import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { getAuthStore } from "@/core/stores/AuthStore";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDecisions } from "./DecisionContext";

type Gate = { target_stage: string; decision_uid: string; round_id: string; target_revision: string; policy_revision: string };
type Stage = { key: string; name: string; is_active: boolean };

export default function DecisionGate() {
    const [t] = useTranslation();
    const { projectUID, cardUID, page, locked, stale, reconfirm, reportError } = useDecisions();
    const { hasRoleAction } = useBoardCard();
    const canEdit = hasRoleAction(ProjectRole.EAction.CardUpdate);
    const [gates, setGates] = useState<Gate[]>([]);
    const [stages, setStages] = useState<Stage[]>([]);
    const [stage, setStage] = useState("");
    const [proposalUID, setProposalUID] = useState("");
    const [removeStage, setRemoveStage] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [needsRead, setNeedsRead] = useState(false);
    const [messageKey, setMessageKey] = useState("");
    const [refresh, setRefresh] = useState(0);
    const generation = useRef(0);
    const writing = useRef(false);
    const readSession = useRef(-1);
    const base = `/board/${projectUID}/card/${cardUID}/decision-gate`;
    const proposals = page?.items.filter((decision) => decision.source_kind === "proposal" && decision.source_ref === cardUID) ?? [];
    const proposal = proposals.find((decision) => decision.uid === proposalUID);
    useEffect(() => {
        const epoch = ++generation.current;
        if (stale) {
            setGates([]);
            setStages([]);
            setConfirmed(false);
            setNeedsRead(true);
            setLoading(false);
            return;
        }
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        setLoading(true);
        setConfirmed(false);
        setRemoveStage("");
        setProposalUID("");
        setStage("");
        Promise.all([
            api.get<{ gates: Gate[] }>(base, { signal: controller.signal }),
            api.get<{ stages: Stage[] }>(`/board/${projectUID}/workflow-stages`, { signal: controller.signal }),
        ])
            .then(([bindings, options]) => {
                if (epoch !== generation.current) return;
                if (session !== getAuthStore().getSessionVersion()) {
                    setGates([]);
                    setStages([]);
                    setNeedsRead(true);
                    reportError(undefined, session);
                    return;
                }
                setGates(bindings.data.gates);
                setStages(options.data.stages.filter((item) => item.is_active));
                readSession.current = session;
                setNeedsRead(false);
            })
            .catch((error) => {
                if (epoch !== generation.current) return;
                setGates([]);
                setStages([]);
                setNeedsRead(true);
                setMessageKey("card.decision.Gate read failed");
                reportError(error, session);
            })
            .finally(() => {
                if (epoch === generation.current) setLoading(false);
            });
        return () => {
            generation.current++;
            controller.abort();
        };
    }, [base, projectUID, page, reconfirm, refresh, stale]);
    const write = async (remove: boolean) => {
        if (!canEdit || locked || loading || needsRead || writing.current || !confirmed) return;
        if (readSession.current !== getAuthStore().getSessionVersion()) {
            setConfirmed(false);
            setNeedsRead(true);
            setGates([]);
            reportError(undefined, readSession.current);
            return;
        }
        if (remove ? !gates.some((item) => item.target_stage === removeStage) : !proposal || !stages.some((item) => item.key === stage)) return;
        const epoch = generation.current;
        const session = readSession.current;
        writing.current = true;
        setBusy(true);
        setConfirmed(false);
        try {
            if (remove) await api.delete(`${base}/${encodeURIComponent(removeStage)}`);
            else
                await api.put(base, {
                    target_stage: stage,
                    decision_uid: proposal!.uid,
                    round_id: proposal!.round_id,
                    target_revision: proposal!.target_revision,
                    policy_revision: proposal!.policy_revision,
                });
            if (epoch !== generation.current) return;
            if (session !== getAuthStore().getSessionVersion()) {
                setGates([]);
                setNeedsRead(true);
                reportError(undefined, session);
                return;
            }
            setMessageKey("card.decision.Gate saved");
            setLoading(true);
            setRefresh((value) => value + 1);
        } catch (error) {
            if (epoch !== generation.current) return;
            // A timed-out mutation may already have committed. Require a read and new confirmation.
            setNeedsRead(true);
            setMessageKey("card.decision.Gate write failed");
            reportError(error, session);
        } finally {
            writing.current = false;
            setBusy(false);
        }
    };
    const disabled = locked || loading || busy || needsRead;
    return (
        <section aria-label={t("card.decision.Stage gates")} className="min-w-0 space-y-2 rounded border p-3">
            <h3 className="font-semibold">{t("card.decision.Stage gates")}</h3>
            <p>{t("card.decision.Gate explanation")}</p>
            {loading && <p>{t("card.decision.Gate loading")}</p>}
            {messageKey && <p aria-live="polite">{t(messageKey)}</p>}
            {!loading && !needsRead && !gates.length && <p>{t("card.decision.No stage gates")}</p>}
            {gates.map((gate) => (
                <div key={gate.target_stage} className="space-y-1 break-all">
                    <p>
                        {t("card.decision.Target stage")}: {stages.find((item) => item.key === gate.target_stage)?.name ?? gate.target_stage} (
                        {gate.target_stage})
                    </p>
                    <p>
                        {t("card.decision.Gate proposal")}:{" "}
                        {proposals.find((item) => item.uid === gate.decision_uid)?.question ?? t("card.decision.Gate proposal unavailable")} (
                        {gate.decision_uid})
                    </p>
                    <p>
                        {t("card.decision.Round")}: {gate.round_id}
                    </p>
                    <p>
                        {t("card.decision.Target revision")}: {gate.target_revision}
                    </p>
                    <p>
                        {t("card.decision.Policy revision")}: {gate.policy_revision}
                    </p>
                    {canEdit && (
                        <Button
                            size="sm"
                            disabled={disabled}
                            onClick={() => {
                                setRemoveStage(gate.target_stage);
                                setConfirmed(false);
                            }}
                        >
                            {t("card.decision.Remove stage gate", { stage: gate.target_stage })}
                        </Button>
                    )}
                </div>
            ))}
            {canEdit && (
                <form
                    className="space-y-2"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void write(!!removeStage);
                    }}
                >
                    <fieldset disabled={disabled} className="space-y-2">
                        {removeStage ? (
                            <p>{t("card.decision.Remove stage gate", { stage: removeStage })}</p>
                        ) : (
                            <>
                                <label className="block">
                                    {t("card.decision.Target stage")}
                                    <select
                                        className="block w-full rounded border bg-background p-2"
                                        value={stage}
                                        onChange={(event) => {
                                            setStage(event.target.value);
                                            setConfirmed(false);
                                        }}
                                    >
                                        <option value="">{t("card.decision.Select target stage")}</option>
                                        {stages.map((item) => (
                                            <option key={item.key} value={item.key}>
                                                {item.name} ({item.key})
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    {t("card.decision.Gate proposal")}
                                    <select
                                        className="block w-full rounded border bg-background p-2"
                                        value={proposalUID}
                                        onChange={(event) => {
                                            setProposalUID(event.target.value);
                                            setConfirmed(false);
                                        }}
                                    >
                                        <option value="">{t("card.decision.Select gate proposal")}</option>
                                        {proposals.map((item) => (
                                            <option key={item.uid} value={item.uid}>
                                                {item.question} ({item.uid})
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                {proposal && (
                                    <div className="break-all text-sm">
                                        <p>
                                            {t("card.decision.Round")}: {proposal.round_id}
                                        </p>
                                        <p>
                                            {t("card.decision.Target revision")}: {proposal.target_revision}
                                        </p>
                                        <p>
                                            {t("card.decision.Policy revision")}: {proposal.policy_revision}
                                        </p>
                                    </div>
                                )}
                            </>
                        )}
                        <label className="flex items-start gap-2">
                            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                            {t("card.decision.Confirm stage gate")}
                        </label>
                    </fieldset>
                    <Button type="submit" disabled={disabled || !confirmed || (!removeStage && (!proposal || !stage))}>
                        {t(removeStage ? "card.decision.Confirm gate removal" : "card.decision.Save stage gate")}
                    </Button>
                    {removeStage && (
                        <Button
                            type="button"
                            disabled={disabled}
                            onClick={() => {
                                setRemoveStage("");
                                setConfirmed(false);
                            }}
                        >
                            {t("card.decision.Cancel gate removal")}
                        </Button>
                    )}
                </form>
            )}
            {needsRead && (
                <Button
                    type="button"
                    disabled={busy || loading || locked}
                    onClick={() => {
                        setMessageKey("");
                        setRefresh((value) => value + 1);
                    }}
                >
                    {t("card.decision.Reload stage gates")}
                </Button>
            )}
        </section>
    );
}
