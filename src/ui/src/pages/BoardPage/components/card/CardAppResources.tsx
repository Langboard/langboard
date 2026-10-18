import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface Resource {
    resource_uid: string;
    connection_uid: string;
    app_key: string;
    name: string;
}
interface Selection {
    revision: number | null;
    resource_uids: string[];
}

export default function CardAppResources(props: { projectUID: string; cardUID: string; userUID: string; embedded?: boolean }) {
    return <ResourceEditor key={`${props.userUID}/${props.projectUID}/${props.cardUID}`} {...props} />;
}

function ResourceEditor({
    projectUID,
    cardUID,
    userUID,
    embedded = false,
}: {
    projectUID: string;
    cardUID: string;
    userUID: string;
    embedded?: boolean;
}) {
    const [t] = useTranslation();
    const text = (key: string) => t(`card.appResources.${key}`);
    const [expanded, setOpen] = useState(false);
    const open = embedded || expanded;
    const [cursor, setCursor] = useState<string>();
    const [connection, setConnection] = useState("");
    const [draft, setDraft] = useState<string[] | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef(false);
    const config = { env: { interceptToast: true } as never };
    const resources = useQuery({
        queryKey: ["card-resource-context", userUID, projectUID, cursor],
        queryFn: async ({ signal }) =>
            (
                await api.get<{ items: Resource[]; next_cursor: string | null }>(`/board/${projectUID}/apps/resource-context`, {
                    ...config,
                    signal,
                    params: { cursor },
                })
            ).data,
        enabled: open,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const [retainedCursor, setRetainedCursor] = useState<string>();
    const retained = useQuery({
        queryKey: ["card-resource-selections", userUID, projectUID, cardUID, retainedCursor],
        queryFn: async ({ signal }) =>
            (
                await api.get<{ items: (Selection & { app_key: string; connection_uid: string })[]; next_cursor: string | null }>(
                    `/board/${projectUID}/card/${cardUID}/apps/resource-selections`,
                    { ...config, signal, params: { after: retainedCursor } }
                )
            ).data,
        enabled: open,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const path = `/board/${projectUID}/card/${cardUID}/apps/connections/${connection}/resources`;
    const selection = useQuery({
        queryKey: ["card-app-resources", userUID, projectUID, cardUID, connection],
        queryFn: async ({ signal }) => (await api.get<Selection>(path, { ...config, signal })).data,
        enabled: open && !!connection,
        retry: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    });
    const busy = pending || retained.isFetching || resources.isFetching || (!!connection && selection.isFetching);
    const unavailable = failed || retained.isError || resources.isError || (!!connection && selection.isError);
    const selected = draft ?? selection.data?.resource_uids ?? [];
    const refresh = async () => {
        if (writing.current) return;
        setPending(true);
        const results = await Promise.all([resources.refetch(), retained.refetch(), ...(connection ? [selection.refetch()] : [])]);
        setFailed(results.some((result) => result.isError));
        setDraft(null);
        setPending(false);
    };
    const save = async () => {
        if (writing.current || busy || unavailable || !selection.data || draft === null) return;
        writing.current = true;
        setPending(true);
        try {
            await api.put(path, { resource_uids: draft, expected_revision: selection.data.revision }, config);
            const result = await selection.refetch();
            setFailed(result.isError);
            setDraft(null);
        } catch {
            setFailed(true);
        } finally {
            setPending(false);
            writing.current = false;
        }
    };
    const connections = [
        ...new Map<string, { app_key: string; connection_uid: string; name: string }>([
            ...(retained.data?.items.map((item) => [item.connection_uid, { ...item, name: text("retained") }] as const) ?? []),
            ...(resources.data?.items.map((item) => [item.connection_uid, item] as const) ?? []),
        ]).values(),
    ];
    return (
        <section className={embedded ? "space-y-3" : "space-y-3 rounded-lg border p-3"} aria-busy={busy}>
            {!embedded && (
                <Button size="sm" variant="ghost" aria-expanded={open} disabled={pending} onClick={() => setOpen(!open)}>
                    {text("title")}
                </Button>
            )}
            {open && (
                <>
                    <p className="text-sm text-muted-foreground">{text("help")}</p>
                    {unavailable && (
                        <p role="alert" className="text-sm">
                            {text("refresh")}
                        </p>
                    )}
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void refresh()}>
                        {t("common.Retry")}
                    </Button>
                    {busy && <p role="status">{t("common.Loading...")}</p>}
                    <label className="block text-sm">
                        <span>{text("connection")}</span>
                        <select
                            className="mt-1 h-10 w-full rounded-md border bg-background px-3"
                            value={connection}
                            disabled={busy || unavailable || draft !== null}
                            onChange={(event) => {
                                setConnection(event.target.value);
                                setDraft(null);
                            }}
                        >
                            <option value="">{text("choose")}</option>
                            {connections.map((item) => (
                                <option key={item.connection_uid} value={item.connection_uid}>
                                    {item.app_key} · {item.name}
                                </option>
                            ))}
                        </select>
                    </label>
                    {!!connection && selection.data && (
                        <>
                            <ul className="space-y-2">
                                {resources.data?.items
                                    .filter((item) => item.connection_uid === connection)
                                    .map((item) => (
                                        <li key={item.resource_uid}>
                                            <Checkbox
                                                label={item.name}
                                                checked={selected.includes(item.resource_uid)}
                                                disabled={busy || unavailable || (!selected.includes(item.resource_uid) && selected.length >= 20)}
                                                onCheckedChange={(checked) =>
                                                    setDraft(
                                                        checked
                                                            ? [...selected, item.resource_uid]
                                                            : selected.filter((uid) => uid !== item.resource_uid)
                                                    )
                                                }
                                            />
                                        </li>
                                    ))}
                            </ul>
                            <p className="text-xs text-muted-foreground">
                                {text("selected")}: {selected.length}/20
                            </p>
                            <div className="flex flex-wrap gap-2">
                                <Button size="sm" disabled={busy || unavailable || draft === null} onClick={() => void save()}>
                                    {t("common.Save")}
                                </Button>
                                <Button size="sm" variant="ghost" disabled={busy || unavailable || !selected.length} onClick={() => setDraft([])}>
                                    {text("clear")}
                                </Button>
                                <Button size="sm" variant="ghost" disabled={busy || draft === null} onClick={() => setDraft(null)}>
                                    {t("common.Cancel")}
                                </Button>
                            </div>
                        </>
                    )}
                    {resources.data?.next_cursor && (
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy || unavailable || draft !== null}
                            onClick={() => setCursor(resources.data!.next_cursor!)}
                        >
                            {text("more")}
                        </Button>
                    )}
                    {retained.data?.next_cursor && (
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy || unavailable || draft !== null}
                            onClick={() => setRetainedCursor(retained.data!.next_cursor!)}
                        >
                            {text("moreRetained")}
                        </Button>
                    )}
                    {retainedCursor && (
                        <Button size="sm" variant="ghost" disabled={busy || draft !== null} onClick={() => setRetainedCursor(undefined)}>
                            {text("firstRetained")}
                        </Button>
                    )}
                    {cursor && (
                        <Button size="sm" variant="ghost" disabled={busy || draft !== null} onClick={() => setCursor(undefined)}>
                            {text("first")}
                        </Button>
                    )}
                </>
            )}
        </section>
    );
}
