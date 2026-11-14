import BoardMcpClientInstall from "./BoardMcpClientInstall";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import { api } from "@/core/helpers/Api";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Scope = "read" | "write";
interface Connection {
    uid: string;
    client_id: string;
}
interface Catalog {
    server: { name: string; transport_url: string | null; resource: string | null; enabled: boolean };
    connections: Connection[];
}
interface Consent {
    connection_uid: string;
    project_uid: string;
    revision: number | null;
    scopes: Scope[];
    available_scopes: Scope[];
}
const config = { env: { interceptToast: true } as never };

function BoardConsent({ connection, projectUID, userUID }: { connection: Connection; projectUID: string; userUID: string }) {
    const [t] = useTranslation();
    const [draft, setDraft] = useState<Scope[] | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const writing = useRef(false);
    const path = `/mcp/connections/${encodeURIComponent(connection.uid)}/boards/${encodeURIComponent(projectUID)}`;
    const query = useQuery({
        queryKey: ["native-mcp-board-consent", userUID, connection.uid, projectUID],
        queryFn: async ({ signal }) => (await api.get<Consent>(path, { ...config, signal })).data,
        retry: false,
        staleTime: 0,
        refetchOnWindowFocus: false,
    });
    const consent = query.data;
    const valid =
        consent?.connection_uid === connection.uid &&
        consent.project_uid === projectUID &&
        Array.isArray(consent.scopes) &&
        Array.isArray(consent.available_scopes) &&
        consent.available_scopes.every((scope) => scope === "read" || scope === "write");
    const disabled = pending || query.isFetching || query.isError || failed || !valid;
    const scopes = draft ?? consent?.scopes ?? [];
    const refresh = async () => {
        const result = await query.refetch();
        setFailed(result.isError);
        if (!result.isError) setDraft(null);
    };
    const save = async (nextScopes: Scope[]) => {
        if (disabled || writing.current || !consent) return;
        writing.current = true;
        setPending(true);
        try {
            await api.put(path, { scopes: nextScopes, expected_revision: consent.revision }, config);
            await refresh();
        } catch {
            setFailed(true);
        } finally {
            writing.current = false;
            setPending(false);
        }
    };
    return (
        <section className="space-y-3 rounded-md border p-3" aria-busy={pending || query.isFetching}>
            <h3 className="text-sm font-semibold">{t("mcp.Board access")}</h3>
            <p className="break-all text-xs">{connection.client_id}</p>
            <p className="text-xs text-muted-foreground">{t("mcp.Board access help")}</p>
            {(["read", "write"] as Scope[]).map((scope) => (
                <Checkbox
                    key={scope}
                    label={t(`mcp.Scope ${scope}`)}
                    checked={scopes.includes(scope)}
                    disabled={disabled || !consent?.available_scopes.includes(scope)}
                    onCheckedChange={(checked) =>
                        setDraft(checked === true ? [...scopes.filter((value) => value !== scope), scope] : scopes.filter((value) => value !== scope))
                    }
                />
            ))}
            {(failed || query.isError) && (
                <p role="alert" className="text-sm">
                    {t("mcp.Refresh required")}
                </p>
            )}
            {query.isPending && <p role="status">{t("mcp.Loading connections")}</p>}
            <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={disabled} onClick={() => void save(scopes)}>
                    {t("mcp.Save board access")}
                </Button>
                <Button size="sm" variant="outline" disabled={disabled || !consent?.scopes.length} onClick={() => void save([])}>
                    {t("mcp.Revoke board access")}
                </Button>
                <Button size="sm" variant="ghost" disabled={pending || query.isFetching} onClick={() => void refresh()}>
                    {t("mcp.Refresh")}
                </Button>
            </div>
        </section>
    );
}

export default function BoardMcpConnection({ projectUID, projectTitle, userUID }: { projectUID: string; projectTitle: string; userUID: string }) {
    const [t] = useTranslation();
    const [selected, setSelected] = useState("");
    const query = useQuery({
        queryKey: ["native-mcp-connections", userUID],
        queryFn: async ({ signal }) => (await api.get<Catalog>("/mcp/connections", { ...config, signal })).data,
        retry: false,
        staleTime: 0,
    });
    const catalog = !query.isError && !query.isFetching ? query.data : undefined;
    const connection = catalog?.connections.find((item) => item.uid === selected);
    const server = catalog?.server;
    const installURL = server?.enabled && server.transport_url?.startsWith("https://") ? server.transport_url : null;
    return (
        <div className="h-full space-y-4 overflow-y-auto p-3" aria-busy={query.isFetching}>
            <h2 className="text-sm font-semibold">{t("mcp.Langboard connection")}</h2>
            <p className="break-words text-sm font-medium">{projectTitle}</p>
            <p className="text-sm">{t("mcp.One connection help")}</p>
            {query.isPending && <p role="status">{t("mcp.Loading connections")}</p>}
            {query.isError && <p role="alert">{t("mcp.Refresh required")}</p>}
            <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
                {t("mcp.Refresh connections")}
            </Button>
            {catalog && (
                <>
                    {installURL ? (
                        <BoardMcpClientInstall key={`${userUID}:${installURL}`} name={server!.name} url={installURL} resource={server!.resource} />
                    ) : (
                        <p role="status" className="text-sm">
                            {t("mcp.Native connection unavailable")}
                        </p>
                    )}
                    <label className="block space-y-2 text-sm">
                        <span>{t("mcp.Choose connection")}</span>
                        <select
                            className="w-full rounded-md border bg-background p-2"
                            value={selected}
                            onChange={(event) => setSelected(event.target.value)}
                        >
                            <option value="">{t("mcp.Choose connection")}</option>
                            {catalog.connections.map((item) => (
                                <option key={item.uid} value={item.uid}>
                                    {item.client_id} ({item.uid})
                                </option>
                            ))}
                        </select>
                    </label>
                    {!catalog.connections.length && (
                        <p role="status" className="text-sm">
                            {t("mcp.No verified connections")}
                        </p>
                    )}
                    {connection && (
                        <BoardConsent
                            key={`${userUID}:${projectUID}:${connection.uid}`}
                            connection={connection}
                            projectUID={projectUID}
                            userUID={userUID}
                        />
                    )}
                </>
            )}
        </div>
    );
}
