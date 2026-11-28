import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import { getAuthStore } from "@/core/stores/AuthStore";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDecisions } from "../../decision/DecisionContext";

export type ActorSource = { kind: "user" | "group" | "team"; uid: string };
type Candidate = ActorSource & { label: string };
type Abilities = { select_user: boolean; select_group: boolean; select_team: boolean };
export type ActorPreview = {
    snapshot_version: number;
    source_kind: string;
    authorization: string;
    project_uid: string;
    card_uid: string;
    actor_uids: string[];
    actors: { uid: string; firstname: string; lastname: string; username: string; sources: ActorSource[] }[];
    sources: (Candidate & { revision: string; membership_count: number; eligible_count: number; excluded_count: number })[];
    eligible_count: number;
    excluded_count: number;
    membership_revision: string;
    policy_revision: string;
    snapshot: string;
    abilities: Abilities;
    execution_authority: false;
};
export type ActorSelection = { preview: ActorPreview; sources: ActorSource[]; confirmed: boolean } | undefined;
const same = (left: ActorSource, right: ActorSource) => left.kind === right.kind && left.uid === right.uid;
export default function DecisionParticipantSelector({ sourceKind, onChange }: { sourceKind: string; onChange: (value: ActorSelection) => void }) {
    const [t] = useTranslation();
    const { projectUID, cardUID, locked, stale, reconfirm, reportError } = useDecisions();
    const [kind, setKind] = useState<ActorSource["kind"]>("user");
    const [query, setQuery] = useState("");
    const [page, setPage] = useState(1);
    const [candidates, setCandidates] = useState<{ records: Candidate[]; total_records: number; abilities?: Abilities }>({
        records: [],
        total_records: 0,
    });
    const [sources, setSources] = useState<Candidate[]>([]);
    const [selection, setSelection] = useState<ActorSelection>();
    const [loading, setLoading] = useState(false);
    const [previewing, setPreviewing] = useState(false);
    const epoch = useRef(0);
    const activePreview = useRef<AbortController | undefined>(undefined);
    const invalidate = () => {
        epoch.current++;
        activePreview.current?.abort();
        setPreviewing(false);
        setSelection(undefined);
        onChange(undefined);
    };
    useEffect(() => {
        invalidate();
        return () => {
            epoch.current++;
            activePreview.current?.abort();
        };
    }, [reconfirm, sourceKind, projectUID, cardUID]);
    useEffect(() => {
        if (stale) {
            setSources([]);
            setCandidates({ records: [], total_records: 0 });
            return;
        }
        let current = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        setLoading(true);
        setCandidates((previous) => ({ ...previous, records: [], total_records: 0 }));
        const timer = window.setTimeout(() => {
            api.get(`/board/${projectUID}/card/${cardUID}/decision-actors/candidates`, {
                params: { source_kind: sourceKind, kind, query, page, limit: 15 },
                signal: controller.signal,
            })
                .then(({ data }) => {
                    if (current && session === getAuthStore().getSessionVersion())
                        setCandidates(
                            data.source_kind === sourceKind && data.authorization === `${sourceKind}_response_candidate`
                                ? data
                                : { records: [], total_records: 0 }
                        );
                })
                .catch((error) => {
                    if (current) {
                        setCandidates({ records: [], total_records: 0 });
                        invalidate();
                        reportError(error, session);
                    }
                })
                .finally(() => {
                    if (current) setLoading(false);
                });
        }, 200);
        return () => {
            current = false;
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [projectUID, cardUID, sourceKind, kind, query, page, stale, reconfirm]);
    const preview = async () => {
        if (previewing || locked || !sources.length) return;
        invalidate();
        const currentEpoch = epoch.current;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        activePreview.current = controller;
        setPreviewing(true);
        const requested = sources.map(({ kind: sourceType, uid }) => ({ kind: sourceType, uid }));
        try {
            const { data } = await api.post<ActorPreview>(
                `/board/${projectUID}/card/${cardUID}/decision-actors/preview`,
                { source_kind: sourceKind, sources: requested },
                { signal: controller.signal }
            );
            if (currentEpoch !== epoch.current || session !== getAuthStore().getSessionVersion()) return;
            if (
                data.source_kind !== sourceKind ||
                data.authorization !== `${sourceKind}_response_candidate` ||
                data.project_uid !== projectUID ||
                data.card_uid !== cardUID
            )
                return;
            const next = { preview: data, sources: requested, confirmed: false };
            setSelection(next);
            onChange(next);
        } catch (error) {
            if (currentEpoch === epoch.current) reportError(error, session);
        } finally {
            if (currentEpoch === epoch.current) setPreviewing(false);
        }
    };
    const allowed = candidates.abilities?.[`select_${kind}`];
    return (
        <section className="min-w-0 space-y-3" aria-label={t("card.decision.Participant sources")}>
            <p>{t("card.decision.Source limits")}</p>
            <label className="block">
                {t("card.decision.Source type")}
                <select
                    aria-label={t("card.decision.Source type")}
                    className="w-full rounded border bg-background p-2"
                    value={kind}
                    onChange={(event) => {
                        setKind(event.target.value as ActorSource["kind"]);
                        setPage(1);
                        setQuery("");
                    }}
                >
                    {(["user", "group", "team"] as const).map((value) => (
                        <option key={value} value={value} disabled={value !== "user" && !candidates.abilities?.[`select_${value}`]}>
                            {t(`card.decision.source_${value}`)}
                        </option>
                    ))}
                </select>
            </label>
            {candidates.abilities?.select_group === false && <p>{t("card.decision.Group unavailable")}</p>}
            <label className="block">
                {t("card.decision.Search participants")}
                <Input
                    aria-label={t("card.decision.Search participants")}
                    maxLength={200}
                    value={query}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setPage(1);
                    }}
                />
            </label>
            <ul className="space-y-2">
                {candidates.records.map((source) => (
                    <li key={`${source.kind}:${source.uid}`}>
                        <label className="flex items-start gap-2">
                            <input
                                type="checkbox"
                                checked={sources.some((item) => same(item, source))}
                                disabled={loading || !allowed || (sources.length >= 64 && !sources.some((item) => same(item, source)))}
                                onChange={(event) => {
                                    invalidate();
                                    setSources(event.target.checked ? [...sources, source] : sources.filter((item) => !same(item, source)));
                                }}
                            />
                            {source.label}
                        </label>
                    </li>
                ))}
            </ul>
            <div className="flex flex-wrap gap-2">
                <Button size="sm" type="button" disabled={loading || page <= 1} onClick={() => setPage(page - 1)}>
                    {t("card.decision.Previous participants")}
                </Button>
                <Button size="sm" type="button" disabled={loading || page * 15 >= candidates.total_records} onClick={() => setPage(page + 1)}>
                    {t("card.decision.Next participants")}
                </Button>
            </div>
            <ul className="space-y-2">
                {sources.map((source) => (
                    <li key={`${source.kind}:${source.uid}`} className="flex flex-wrap items-center gap-2">
                        <span>
                            {t(`card.decision.source_${source.kind}`)}: {source.label}
                        </span>
                        <Button
                            type="button"
                            size="sm"
                            aria-label={`${t("card.decision.Remove source")} ${source.label}`}
                            onClick={() => {
                                invalidate();
                                setSources(sources.filter((item) => !same(item, source)));
                            }}
                        >
                            {t("card.decision.Remove source")}
                        </Button>
                    </li>
                ))}
            </ul>
            <Button type="button" disabled={locked || loading || previewing || !sources.length} onClick={() => void preview()}>
                {t("card.decision.Preview participants")}
            </Button>
            {selection && (
                <div className="space-y-2" aria-live="polite">
                    <p>{t("card.decision.Resolved count", { count: selection.preview.eligible_count })}</p>
                    <p>{t("card.decision.Excluded count", { count: selection.preview.excluded_count })}</p>
                    <p>{t("card.decision.Preview authority")}</p>
                    <ul>
                        {selection.preview.sources.map((source) => (
                            <li key={`${source.kind}:${source.uid}`}>
                                {t(`card.decision.source_${source.kind}`)}: {source.label} —{" "}
                                {t("card.decision.Source count", {
                                    eligible: source.eligible_count,
                                    total: source.membership_count,
                                    excluded: source.excluded_count,
                                })}
                            </li>
                        ))}
                    </ul>
                    <ul>
                        {selection.preview.actors.map((person) => (
                            <li key={person.uid}>
                                {[person.firstname, person.lastname].filter(Boolean).join(" ") || person.username} ({person.uid})
                                <ul>
                                    {person.sources.map((source) => (
                                        <li key={`${source.kind}:${source.uid}`}>
                                            {t(`card.decision.source_${source.kind}`)}:{" "}
                                            {selection.preview.sources.find((item) => same(item, source))?.label ?? source.uid}
                                        </li>
                                    ))}
                                </ul>
                            </li>
                        ))}
                    </ul>
                    <label className="flex items-start gap-2">
                        <input
                            type="checkbox"
                            checked={selection.confirmed}
                            onChange={(event) => {
                                const next = { ...selection, confirmed: event.target.checked };
                                setSelection(next);
                                onChange(next);
                            }}
                        />
                        {t("card.decision.Confirm participants", { count: selection.preview.eligible_count })}
                    </label>
                </div>
            )}
        </section>
    );
}
