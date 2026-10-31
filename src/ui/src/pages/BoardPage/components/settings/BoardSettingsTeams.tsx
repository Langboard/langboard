import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import { getAuthStore } from "@/core/stores/AuthStore";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Person = { uid: string; firstname: string; lastname: string; username: string };
type Team = { uid: string; name: string; revision: number; member_uids: string[]; members: Person[]; member_count: number };
type Page<T> = { records: T[]; total_records: number };
type Abilities = { manage: boolean; import_group: boolean };
type Preview = {
    team_revision: number;
    group: { uid: string; display_name: string };
    snapshot: string;
    eligible_count: number;
    existing_count: number;
    excluded_count: number;
    target_count: number;
    records: Person[];
};
type DecisionImpact = { status: "available"; ongoing: boolean; scope: "readable_cards" } | { status: "unavailable" };
type Command = { method: "POST" | "PATCH" | "DELETE"; url: string; data: Record<string, unknown> };
const personName = (person: Person) => `${person.firstname} ${person.lastname}`.trim() || person.username || person.uid;

// A new board/account gets an isolated command receipt and draft lifetime.
export default function BoardSettingsTeams(props: { projectUID: string; accountUID: string; active: boolean }) {
    return <Teams key={`${props.projectUID}:${props.accountUID}`} {...props} />;
}

function Teams({ projectUID, active }: { projectUID: string; active: boolean }) {
    const [t] = useTranslation();
    const label = (key: string, options?: Record<string, unknown>) => t(`board.teams.${key}`, options);
    const base = `/board/${projectUID}/teams`;
    const [query, setQuery] = useState("");
    const [page, setPage] = useState(1);
    const [list, setList] = useState<Page<Team>>({ records: [], total_records: 0 });
    const [abilities, setAbilities] = useState<Abilities>({ manage: false, import_group: false });
    const [selected, setSelected] = useState("");
    const [team, setTeam] = useState<Team>();
    const [name, setName] = useState("");
    const [newName, setNewName] = useState("");
    const [memberQuery, setMemberQuery] = useState("");
    const [memberPage, setMemberPage] = useState(1);
    const [candidates, setCandidates] = useState<Page<Person>>({ records: [], total_records: 0 });
    const [groupQuery, setGroupQuery] = useState("");
    const [groups, setGroups] = useState<{ uid: string; display_name: string }[]>([]);
    const [groupUID, setGroupUID] = useState("");
    const [preview, setPreview] = useState<Preview>();
    const [confirmed, setConfirmed] = useState(false);
    const [decisionImpact, setDecisionImpact] = useState<DecisionImpact>();
    const [deleting, setDeleting] = useState(false);
    const [stale, setStale] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [reload, setReload] = useState(0);
    const [pending, setPending] = useState<Command>();
    const generation = useRef(0);
    const nameDirty = useRef(false);
    const latest = useRef({ active, selected });
    latest.current = { active, selected };
    const resetSnapshot = () => {
        setPreview(undefined);
        setConfirmed(false);
        setDeleting(false);
        setDecisionImpact(undefined);
    };
    const fail = (error: unknown) => {
        const status = (error as { response?: { status?: number } }).response?.status;
        if ([401, 403, 404, 409].includes(status ?? 0)) {
            setStale(true);
            setAbilities({ manage: false, import_group: false });
            setPending(undefined);
            resetSnapshot();
            setMessage(label("Reload required"));
        } else setMessage(label("Request failed"));
    };
    useEffect(() => {
        generation.current++;
        setBusy(false);
        resetSnapshot();
        return () => {
            generation.current++;
        };
    }, [active, selected]);
    useEffect(() => {
        if (!active || stale) return;
        let valid = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            api.get(`${base}`, { params: { query, page, limit: 15 }, signal: controller.signal })
                .then(({ data }) => {
                    if (!valid || session !== getAuthStore().getSessionVersion()) return;
                    setList(data);
                    setAbilities(data.abilities);
                })
                .catch((error) => {
                    if (valid) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
                });
        }, 200);
        return () => {
            valid = false;
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [active, stale, base, query, page, reload]);
    useEffect(() => {
        if (!active || stale || !selected) return;
        let valid = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        setTeam(undefined);
        api.get(`${base}/${selected}`, { signal: controller.signal })
            .then(({ data }) => {
                if (!valid || session !== getAuthStore().getSessionVersion()) return;
                setTeam(data.team);
                if (!nameDirty.current) setName(data.team.name);
            })
            .catch((error) => {
                if (valid) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
            });
        return () => {
            valid = false;
            controller.abort();
        };
    }, [active, stale, base, selected, reload]);
    useEffect(() => {
        if (!active || stale || !selected || !abilities.manage) return;
        let valid = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            api.get(`${base}/candidates`, { params: { query: memberQuery, page: memberPage, limit: 15 }, signal: controller.signal })
                .then(({ data }) => {
                    if (valid && session === getAuthStore().getSessionVersion()) setCandidates(data);
                })
                .catch((error) => {
                    if (valid) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
                });
        }, 200);
        return () => {
            valid = false;
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [active, stale, selected, abilities.manage, base, memberQuery, memberPage, reload]);
    useEffect(() => {
        if (!active || stale || !selected || !abilities.import_group) return;
        let valid = true;
        const session = getAuthStore().getSessionVersion();
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            api.get("/settings/groups", { params: { query: groupQuery, limit: 100 }, signal: controller.signal })
                .then(({ data }) => {
                    if (valid && session === getAuthStore().getSessionVersion()) setGroups(data.records);
                })
                .catch((error) => {
                    if (valid) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
                });
        }, 200);
        return () => {
            valid = false;
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [active, stale, selected, abilities.import_group, groupQuery, reload]);
    const run = async (command: Command) => {
        if (busy || stale || !active || !abilities.manage) return;
        const epoch = generation.current;
        const session = getAuthStore().getSessionVersion();
        const valid = () => epoch === generation.current && latest.current.active && session === getAuthStore().getSessionVersion();
        setPending(command);
        setBusy(true);
        setMessage("");
        try {
            const { data } = await api.request(command);
            if (!valid()) return;
            setPending(undefined);
            nameDirty.current = false;
            resetSnapshot();
            if (data.deleted) {
                setSelected("");
                setTeam(undefined);
            } else setSelected(data.team_uid);
            setReload((value) => value + 1);
            setMessage(label("Saved"));
        } catch (error) {
            if (epoch !== generation.current || !latest.current.active) return;
            const failure = session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } };
            const status = (failure as { response?: { status?: number } }).response?.status;
            if (status && status < 500) setPending(undefined);
            fail(failure);
        } finally {
            if (epoch === generation.current) setBusy(false);
        }
    };
    const command = (method: Command["method"], suffix: string, data: Record<string, unknown>) => {
        if (pending) return;
        void run({ method, url: `${base}${suffix}`, data: { ...data, request_id: crypto.randomUUID() } });
    };
    const loadPreview = async () => {
        const epoch = generation.current;
        const session = getAuthStore().getSessionVersion();
        setBusy(true);
        resetSnapshot();
        try {
            const { data } = await api.get(`${base}/${selected}/group-preview`, { params: { group_uid: groupUID } });
            if (epoch === generation.current && latest.current.active && session === getAuthStore().getSessionVersion()) setPreview(data);
        } catch (error) {
            if (epoch === generation.current) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
        } finally {
            if (epoch === generation.current) setBusy(false);
        }
    };
    const reviewDeletion = async () => {
        const epoch = generation.current;
        const session = getAuthStore().getSessionVersion();
        setBusy(true);
        resetSnapshot();
        try {
            const { data } = await api.get(`${base}/${selected}`);
            if (epoch !== generation.current || !latest.current.active) return;
            if (session !== getAuthStore().getSessionVersion()) {
                fail({ response: { status: 401 } });
                return;
            }
            setTeam(data.team);
            const impact = data.decision_impact;
            setDecisionImpact(
                impact?.status === "available" && typeof impact.ongoing === "boolean" && impact.scope === "readable_cards"
                    ? impact
                    : { status: "unavailable" }
            );
        } catch (error) {
            if (epoch === generation.current) fail(session === getAuthStore().getSessionVersion() ? error : { response: { status: 401 } });
        } finally {
            if (epoch === generation.current) setBusy(false);
        }
    };
    const locked = busy || !!pending || stale;
    const pager = (current: number, total: number, change: (page: number) => void) => (
        <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={locked || current <= 1} onClick={() => change(current - 1)}>
                {label("Previous")}
            </Button>
            <span>{label("Page", { page: current })}</span>
            <Button size="sm" disabled={locked || current * 15 >= total} onClick={() => change(current + 1)}>
                {label("Next")}
            </Button>
        </div>
    );
    return (
        <section aria-label={label("Teams")} className="min-w-0 space-y-3 break-words py-3" aria-busy={busy}>
            <p>{label("Board only")}</p>
            <p role="status" aria-live="polite">
                {message}
            </p>
            <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                    generation.current++;
                    setPending(undefined);
                    resetSnapshot();
                    setStale(false);
                    setReload((value) => value + 1);
                }}
            >
                {label("Reload")}
            </Button>
            {pending && (
                <div role="alert">
                    <p>{label("Retry exact command")}</p>
                    <Button disabled={busy} onClick={() => void run(pending)}>
                        {label("Retry")}
                    </Button>
                </div>
            )}
            <label className="block">
                {label("Search teams")}
                <Input
                    maxLength={200}
                    value={query}
                    disabled={locked}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setPage(1);
                    }}
                />
            </label>
            <ul className="space-y-2">
                {list.records.map((item) => (
                    <li key={item.uid}>
                        <Button
                            className="h-auto max-w-full whitespace-normal text-left"
                            variant={selected === item.uid ? "secondary" : "outline"}
                            disabled={locked}
                            aria-pressed={selected === item.uid}
                            onClick={() => {
                                nameDirty.current = false;
                                setSelected(item.uid);
                                setTeam(undefined);
                                resetSnapshot();
                            }}
                        >
                            {item.name} ({item.member_count})
                        </Button>
                    </li>
                ))}
            </ul>
            {pager(page, list.total_records, setPage)}
            {abilities.manage && (
                <form
                    className="space-y-2"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (newName.trim()) command("POST", "", { name: newName.trim() });
                    }}
                >
                    <label className="block">
                        {label("New team name")}
                        <Input value={newName} maxLength={200} disabled={locked} onChange={(event) => setNewName(event.target.value)} />
                    </label>
                    <Button type="submit" disabled={locked || !newName.trim()}>
                        {label("Create team")}
                    </Button>
                </form>
            )}
            {team && (
                <div className="min-w-0 space-y-3 border-t pt-3">
                    <h3 className="font-semibold">{team.name}</h3>
                    <p>{label("Revision", { revision: team.revision })}</p>
                    {abilities.manage && (
                        <form
                            className="space-y-2"
                            onSubmit={(event) => {
                                event.preventDefault();
                                if (name.trim()) command("PATCH", `/${team.uid}`, { name: name.trim(), expected_revision: team.revision });
                            }}
                        >
                            <label className="block">
                                {label("Team name")}
                                <Input
                                    value={name}
                                    maxLength={200}
                                    disabled={locked}
                                    onChange={(event) => {
                                        nameDirty.current = true;
                                        setName(event.target.value);
                                    }}
                                />
                            </label>
                            <Button type="submit" disabled={locked || !name.trim()}>
                                {label("Rename")}
                            </Button>
                        </form>
                    )}
                    <h4>{label("Members")}</h4>
                    <ul className="space-y-2">
                        {team.members.map((person) => (
                            <li key={person.uid} className="flex flex-wrap items-center gap-2">
                                <span className="min-w-0 break-all">{personName(person)}</span>
                                {abilities.manage && (
                                    <Button
                                        size="sm"
                                        disabled={locked}
                                        aria-label={`${label("Remove")} ${personName(person)}`}
                                        onClick={() =>
                                            command("DELETE", `/${team.uid}/members`, { expected_revision: team.revision, user_uids: [person.uid] })
                                        }
                                    >
                                        {label("Remove")}
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
                    {abilities.manage && (
                        <>
                            <label className="block">
                                {label("Search board participants")}
                                <Input
                                    value={memberQuery}
                                    maxLength={200}
                                    disabled={locked}
                                    onChange={(event) => {
                                        setMemberQuery(event.target.value);
                                        setMemberPage(1);
                                    }}
                                />
                            </label>
                            <ul className="space-y-2">
                                {candidates.records.map((person) => (
                                    <li key={person.uid} className="flex flex-wrap items-center gap-2">
                                        <span className="min-w-0 break-all">{personName(person)}</span>
                                        <Button
                                            size="sm"
                                            disabled={locked || team.member_uids.includes(person.uid)}
                                            aria-label={`${label("Add")} ${personName(person)}`}
                                            onClick={() =>
                                                command("POST", `/${team.uid}/members`, { expected_revision: team.revision, user_uids: [person.uid] })
                                            }
                                        >
                                            {label("Add")}
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                            {pager(memberPage, candidates.total_records, setMemberPage)}
                            {abilities.import_group && (
                                <fieldset className="min-w-0 space-y-2 rounded border p-2" disabled={locked}>
                                    <legend>{label("Canonical groups")}</legend>
                                    <p>{label("Snapshot only")}</p>
                                    <label className="block">
                                        {label("Search groups")}
                                        <Input
                                            value={groupQuery}
                                            maxLength={200}
                                            onChange={(event) => {
                                                setGroupQuery(event.target.value);
                                                setGroupUID("");
                                                resetSnapshot();
                                            }}
                                        />
                                    </label>
                                    <label className="block">
                                        {label("Canonical group")}
                                        <select
                                            aria-label={label("Canonical group")}
                                            className="w-full min-w-0 rounded border bg-background p-2"
                                            value={groupUID}
                                            onChange={(event) => {
                                                setGroupUID(event.target.value);
                                                resetSnapshot();
                                            }}
                                        >
                                            <option value="">{label("Select group")}</option>
                                            {groups.map((group) => (
                                                <option key={group.uid} value={group.uid}>
                                                    {group.display_name}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                    <Button disabled={!groupUID || locked} onClick={() => void loadPreview()}>
                                        {label("Preview")}
                                    </Button>
                                    {preview && (
                                        <div className="space-y-2">
                                            <p>{preview.group.display_name}</p>
                                            {(["eligible", "existing", "excluded", "target"] as const).map((kind) => (
                                                <p key={kind}>{label(kind, { count: preview[`${kind}_count`] })}</p>
                                            ))}
                                            <ul>
                                                {preview.records.map((person) => (
                                                    <li key={person.uid}>{personName(person)}</li>
                                                ))}
                                            </ul>
                                            <label className="flex items-start gap-2">
                                                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                                                {label("Confirm count", { count: preview.target_count })}
                                            </label>
                                            <Button
                                                className="h-auto whitespace-normal"
                                                disabled={locked || !confirmed || preview.target_count === 0}
                                                onClick={() =>
                                                    command("POST", `/${team.uid}/group-add`, {
                                                        expected_revision: preview.team_revision,
                                                        group_uid: preview.group.uid,
                                                        snapshot: preview.snapshot,
                                                        confirmed_count: preview.target_count,
                                                        confirmed: true,
                                                    })
                                                }
                                            >
                                                {label("Import count", { count: preview.target_count })}
                                            </Button>
                                        </div>
                                    )}
                                </fieldset>
                            )}
                            <Button disabled={locked} onClick={() => void reviewDeletion()}>
                                {label("Review deletion")}
                            </Button>
                            {decisionImpact && (
                                <p role="alert">
                                    {label(
                                        decisionImpact.status !== "available"
                                            ? "Decision impact unavailable"
                                            : decisionImpact.ongoing
                                              ? "Decision impact ongoing"
                                              : "Decision impact none"
                                    )}
                                </p>
                            )}
                            <label className="flex items-start gap-2">
                                <input
                                    type="checkbox"
                                    disabled={locked || decisionImpact?.status !== "available"}
                                    checked={deleting}
                                    onChange={(event) => setDeleting(event.target.checked)}
                                />
                                {label("Confirm deletion")}
                            </label>
                            <Button
                                variant="destructive"
                                disabled={locked || !deleting || decisionImpact?.status !== "available"}
                                onClick={() => command("DELETE", `/${team.uid}`, { expected_revision: team.revision })}
                            >
                                {label("Delete team")}
                            </Button>
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
