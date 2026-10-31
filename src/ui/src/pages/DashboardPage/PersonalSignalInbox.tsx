import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/core/helpers/Api";
import { ROUTES } from "@/core/routing/constants";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";

interface PersonalSignal {
    signal_uid: string;
    project_uid: string;
    project_title: string;
    resource_name: string;
    external_id: string;
    commit_sha: string;
}
const uid = /^[A-Za-z0-9]{1,11}$/;
export default function PersonalSignalInbox({ actorScope }: { actorScope: string }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const [items, setItems] = useState<PersonalSignal[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState(false);
    const controller = useRef<AbortController | null>(null);
    const generation = useRef(0);
    const text = (key: string) => t(`dashboard.personalInbox.${key}`);
    const load = async (after?: string) => {
        controller.current?.abort();
        const request = new AbortController();
        controller.current = request;
        const current = ++generation.current;
        setPending(true);
        setError(false);
        // Fresh authority is checked on every page. Discard previous results while
        // loading so a revoked board cannot remain actionable during refresh.
        setItems([]);
        setCursor(null);
        try {
            const response = await api.get<{ items: PersonalSignal[]; next_cursor: string | null }>("/dashboard/signals/inbox", {
                signal: request.signal,
                params: after ? { after } : undefined,
            });
            if (request.signal.aborted || generation.current !== current) return;
            setItems(response.data.items.filter((item) => uid.test(item.signal_uid) && uid.test(item.project_uid)));
            setCursor(response.data.next_cursor);
        } catch {
            if (!request.signal.aborted && generation.current === current) setError(true);
        } finally {
            if (generation.current === current) setPending(false);
        }
    };
    useEffect(() => {
        void load();
        const refresh = () => void load();
        window.addEventListener("focus", refresh);
        return () => {
            generation.current++;
            controller.current?.abort();
            window.removeEventListener("focus", refresh);
        };
    }, [actorScope]);
    return (
        <section className="mx-auto w-full max-w-4xl p-4" aria-label={text("title")}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h1 className="text-xl font-semibold">{text("title")}</h1>
                <button className="btn btn-sm" type="button" disabled={pending} onClick={() => void load()}>
                    {t("card.inbox.refresh")}
                </button>
            </div>
            <p className="mb-4 text-sm">{text("hint")}</p>
            {pending && <p role="status">{t("card.inbox.loading")}</p>}
            {error && <p role="alert">{t("card.inbox.error")}</p>}
            {!pending && !error && !items.length && <p>{t("card.inbox.empty")}</p>}
            <ul className="list">
                {items.map((item) => (
                    <li className="list-row" key={item.signal_uid}>
                        <div className="min-w-0 space-y-1">
                            <p className="break-words font-semibold">{item.project_title}</p>
                            <p className="break-all text-sm">
                                {t("dashboard.personalInbox.sourceReference", {
                                    resource: item.resource_name,
                                    externalId: item.external_id,
                                    commit: item.commit_sha.slice(0, 12),
                                })}
                            </p>
                            <button
                                className="btn btn-sm"
                                type="button"
                                onClick={() =>
                                    navigate(`${ROUTES.BOARD.MAIN(item.project_uid)}?context=inbox&signal=${encodeURIComponent(item.signal_uid)}`)
                                }
                            >
                                {text("open source")}
                            </button>
                        </div>
                    </li>
                ))}
            </ul>
            {cursor && (
                <button className="btn mt-4" type="button" disabled={pending} onClick={() => void load(cursor)}>
                    {text("next")}
                </button>
            )}
        </section>
    );
}
