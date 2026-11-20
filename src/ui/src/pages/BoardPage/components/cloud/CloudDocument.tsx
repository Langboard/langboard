import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Input from "@/components/base/Input";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { CloudItem } from "@/controllers/api/board/cloud/useBoardCloud";

type Source = {
    source_type: "project_attachment" | "wiki_attachment";
    project_uid: string;
    wiki_uid?: string;
    attachment_uid: string;
    name: string;
    url: string;
    board_url: string;
};
type Receipt = { attachment_uid: string; generation: string; status: "pending" | "processing" | "indexed" | "failed" };
type DocumentPage = Omit<Receipt, "attachment_uid"> & {
    source: Source;
    text: string;
    total_chars: number;
    next_offset: number | null;
    keywords: Record<string, string[]>;
    embedding_status: string;
};
type SearchResult = {
    source: Source;
    generation: string;
    matches: { chunk_id: string; content: string; source: Source; pages: number[]; page: number | null }[];
};

/** Explicit per-file requests retain private document content only while this authorized dialog is mounted. */
export default function CloudDocument({ projectUID, item, onClose }: { projectUID: string; item: CloudItem; onClose: () => void }) {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const [document, setDocument] = useState<DocumentPage>();
    const [matches, setMatches] = useState<SearchResult>();
    const [status, setStatus] = useState<Receipt["status"]>();
    const [query, setQuery] = useState("");
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState(false);
    const active = useRef<AbortController | undefined>(undefined);
    const mounted = useRef(false);
    const wikiUID = item.source.kind === "wiki" ? item.source.wiki_uid : undefined;
    const ownerPath = wikiUID ? `/wiki/${encodeURIComponent(wikiUID)}` : "";
    const path = `/board/${encodeURIComponent(projectUID)}/cloud${ownerPath}/attachment/${encodeURIComponent(item.attachment_uid!)}/document`;
    const current = () =>
        mounted.current && useAuthStore.getState().currentUser?.uid === actor && useAuthStore.getState().getIdentityVersion() === identity;
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            active.current?.abort();
        };
    }, []);
    const validSourceURL = (value: string) => {
        if (value === item.url) return true;
        if (typeof value !== "string" || !value.startsWith("/file/")) return false;
        // Published output may retain its earlier native file locator after replacement.
        const url = new URL(value, window.location.origin);
        return url.origin === window.location.origin && url.pathname.startsWith("/file/") && !url.search && !url.hash;
    };
    const validSource = (source: Source) =>
        source?.source_type === (wikiUID ? "wiki_attachment" : "project_attachment") &&
        (wikiUID ? source.wiki_uid === wikiUID : source.wiki_uid === undefined) &&
        source.project_uid === projectUID &&
        source.attachment_uid === item.attachment_uid &&
        validSourceURL(source.url) &&
        source.board_url === (wikiUID ? `/board/${projectUID}/wiki/${wikiUID}` : `/board/${projectUID}/cloud`);
    async function run(operation: (signal: AbortSignal) => Promise<void>) {
        if (busy || !current()) return;
        active.current?.abort();
        const controller = new AbortController();
        active.current = controller;
        setBusy(true);
        setFailed(false);
        setDocument(undefined);
        setMatches(undefined);
        setStatus(undefined);
        try {
            await operation(controller.signal);
        } catch {
            if (current() && !controller.signal.aborted) {
                setDocument(undefined);
                setMatches(undefined);
                setStatus(undefined);
                setFailed(true);
            }
        } finally {
            if (current() && !controller.signal.aborted) setBusy(false);
        }
    }
    const options = (signal: AbortSignal) => ({ signal, env: { interceptToast: true } as never });
    async function read(offset = 0) {
        const generation = offset ? document?.generation : undefined;
        await run(async (signal) => {
            const { data } = await api.get<DocumentPage>(path, { ...options(signal), params: { offset, limit: 4000 } });
            if (!current() || signal.aborted) return;
            if (
                !validSource(data.source) ||
                !["pending", "processing", "indexed", "failed"].includes(data.status) ||
                typeof data.text !== "string" ||
                [...data.text].length > 4000 ||
                !Number.isInteger(data.total_chars) ||
                data.total_chars < 0 ||
                (data.next_offset !== null &&
                    (!Number.isInteger(data.next_offset) || data.next_offset <= offset || data.next_offset > data.total_chars)) ||
                (generation && data.generation !== generation)
            )
                throw new Error("Document source changed");
            setDocument(data);
            setStatus(data.status);
        });
    }
    async function process() {
        await run(async (signal) => {
            const { data } = await api.post<Receipt>(`${path}/process`, undefined, options(signal));
            if (!current() || signal.aborted) return;
            if (data.attachment_uid !== item.attachment_uid || !["pending", "processing", "indexed", "failed"].includes(data.status))
                throw new Error("Invalid processing receipt");
            setStatus(data.status);
        });
    }
    async function search() {
        const text = query.trim();
        if (!text || text.length > 1000) return;
        await run(async (signal) => {
            const { data } = await api.post<SearchResult>(`${path}/search`, { query: text, max_tokens: 2000 }, options(signal));
            if (!current() || signal.aborted) return;
            if (
                !validSource(data.source) ||
                !Array.isArray(data.matches) ||
                data.matches.length > 100 ||
                data.matches.some((match) => !validSource(match.source) || typeof match.content !== "string" || typeof match.chunk_id !== "string")
            )
                throw new Error("Invalid search source");
            setMatches(data);
        });
    }
    return (
        <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
            <Dialog.Content className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
                <Dialog.Header>
                    <Dialog.Title>
                        {t("cloud.Document")}: {item.name}
                    </Dialog.Title>
                    <Dialog.Description>{t("cloud.Document help")}</Dialog.Description>
                </Dialog.Header>
                <div className="space-y-3">
                    <div className="flex flex-wrap gap-2">
                        <Button size="sm" disabled={busy} onClick={() => void read()}>
                            {t("cloud.Read document")}
                        </Button>
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => void process()}>
                            {t("cloud.Process document")}
                        </Button>
                        <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-sm underline">
                            {t("cloud.Original file")}
                        </a>
                    </div>
                    {busy && <p role="status">{t("cloud.Document loading")}</p>}
                    {failed && <p role="alert">{t("cloud.Document unavailable")}</p>}
                    {status && <p role="status">{t(`cloud.Document ${status}`)}</p>}
                    {document && (
                        <div className="space-y-2">
                            <a href={document.source.url} target="_blank" rel="noopener noreferrer" className="text-xs underline">
                                {t("cloud.Source")}: {document.source.name}
                            </a>
                            <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-sm" aria-label={t("cloud.Document text")}>
                                {document.text}
                            </pre>
                            <p className="text-xs">{t("cloud.Document characters", { count: document.total_chars })}</p>
                            <p className="text-xs">
                                {t(document.embedding_status === "indexed" ? "cloud.Search ready" : "cloud.Search unavailable")}
                            </p>
                            {document.next_offset !== null && (
                                <Button size="sm" disabled={busy} onClick={() => void read(document.next_offset!)}>
                                    {t("cloud.Next document page")}
                                </Button>
                            )}
                        </div>
                    )}
                    <form
                        className="space-y-2"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void search();
                        }}
                    >
                        <Input
                            aria-label={t("cloud.Document query")}
                            value={query}
                            maxLength={1000}
                            onChange={(event) => setQuery(event.target.value)}
                        />
                        <Button type="submit" size="sm" disabled={busy || !query.trim() || query.trim().length > 1000}>
                            {t("cloud.Search document")}
                        </Button>
                    </form>
                    {matches && (
                        <div aria-label={t("cloud.Document matches")} className="space-y-3">
                            {!matches.matches.length && <p role="status">{t("cloud.No document matches")}</p>}
                            {matches.matches.map((match) => (
                                <article key={match.chunk_id} className="space-y-1 rounded border p-2">
                                    <p className="whitespace-pre-wrap break-words text-sm">{match.content}</p>
                                    <a href={match.source.url} target="_blank" rel="noopener noreferrer" className="text-xs underline">
                                        {t("cloud.Source")}: {match.source.name}
                                    </a>
                                    {(match.page || match.pages?.length > 0) && (
                                        <p className="text-xs">
                                            {t("cloud.Document pages", { pages: match.pages?.length ? match.pages.join(", ") : match.page })}
                                        </p>
                                    )}
                                </article>
                            ))}
                        </div>
                    )}
                </div>
            </Dialog.Content>
        </Dialog.Root>
    );
}
