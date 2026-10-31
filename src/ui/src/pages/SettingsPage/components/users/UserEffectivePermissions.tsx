import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import useSearchCardContext from "@/controllers/api/card/useSearchCardContext";
import Select from "@/components/base/Select";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import useUserEffectivePermissions from "@/controllers/api/settings/users/useUserEffectivePermissions";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

export default function UserEffectivePermissions({ uid }: { uid: string }) {
    const [t] = useTranslation();
    const selectID = useId();
    const [selectedUID, setSelectedUID] = useState("");
    const [cardQuery, setCardQuery] = useState("");
    const [selectedCardUID, setSelectedCardUID] = useState("");
    const cardSelectID = useId();
    const cardSearchID = useId();
    const actionSelectID = useId();
    const [action, setAction] = useState<"read" | "card_update">("read");
    const catalog = useGetProjects({ staleTime: 0, refetchOnMount: "always", interceptToast: true });
    const projects = !catalog.isFetching && !catalog.isError ? (catalog.data?.projects ?? []) : [];
    const projectUID = projects.some((project) => project.uid === selectedUID) ? selectedUID : "";
    const cardSearch = useSearchCardContext(projectUID, cardQuery, !!projectUID, { staleTime: 0, refetchOnMount: "always" });
    const cards = !cardSearch.isFetching && !cardSearch.isError && cardQuery.length >= 2 ? (cardSearch.data?.cards ?? []) : [];
    const cardUID = cards.some((card) => card.uid === selectedCardUID) ? selectedCardUID : "";
    const { data, loading, error, retry } = useUserEffectivePermissions(uid, projectUID, cardUID, cardUID ? action : "read");
    const label = t("settings.Effective board read permission");
    return (
        <section aria-label={label} aria-busy={catalog.isFetching || loading} className="space-y-2 border-t pt-4">
            <h3 className="font-semibold">{label}</h3>
            <p className="text-sm text-muted-foreground">{t("settings.Effective board read description")}</p>
            {catalog.isFetching && <p role="status">{t("common.Loading...")}</p>}
            {catalog.isError && (
                <div className="space-y-2">
                    <p role="alert">{t("dashboard.Could not load projects")}</p>
                    <Button variant="outline" size="sm" onClick={() => void catalog.refetch()}>
                        {t("common.Retry")}
                    </Button>
                </div>
            )}
            {!catalog.isFetching && !catalog.isError && !projects.length && <p>{t("settings.No readable boards")}</p>}
            {!!projects.length && (
                <>
                    <label htmlFor={selectID} className="text-sm font-medium">
                        {t("settings.Select readable board")}
                    </label>
                    <Select.Root
                        value={projectUID}
                        onValueChange={(value) => {
                            setSelectedUID(value);
                            setCardQuery("");
                            setSelectedCardUID("");
                            setAction("read");
                        }}
                    >
                        <Select.Trigger id={selectID}>
                            <Select.Value placeholder={t("settings.Select readable board")} />
                        </Select.Trigger>
                        <Select.Content>
                            {projects.map((project) => (
                                <Select.Item key={project.uid} value={project.uid}>
                                    {project.title || project.uid}
                                </Select.Item>
                            ))}
                        </Select.Content>
                    </Select.Root>
                    {!projectUID && <p className="text-sm">{t("settings.Select board to inspect")}</p>}
                </>
            )}
            {projectUID && (
                <div className="space-y-2">
                    <h4 className="font-medium">{t("settings.Effective card permissions")}</h4>
                    <p className="text-sm">{t("settings.Card permission inspection description")}</p>
                    <label htmlFor={cardSearchID}>{t("settings.Search readable cards")}</label>
                    <Input
                        id={cardSearchID}
                        value={cardQuery}
                        maxLength={1000}
                        onChange={(event) => {
                            setCardQuery(event.target.value);
                            setSelectedCardUID("");
                            setAction("read");
                        }}
                    />
                    {cardSearch.isFetching && <p role="status">{t("common.Loading...")}</p>}
                    {cardSearch.isError && <p role="alert">{t("settings.Card search failed")}</p>}
                    {cardQuery.length >= 2 && !cardSearch.isFetching && !cardSearch.isError && !cards.length && (
                        <p>{t("settings.No readable cards")}</p>
                    )}
                    {!!cards.length && (
                        <>
                            <label htmlFor={cardSelectID}>{t("card.Select cards")}</label>
                            <Select.Root
                                value={cardUID}
                                onValueChange={(value) => {
                                    setSelectedCardUID(value);
                                    setAction("read");
                                }}
                            >
                                <Select.Trigger id={cardSelectID}>
                                    <Select.Value placeholder={t("card.Select cards")} />
                                </Select.Trigger>
                                <Select.Content>
                                    {cards.map((card) => (
                                        <Select.Item key={card.uid} value={card.uid}>
                                            {card.title || card.uid}
                                        </Select.Item>
                                    ))}
                                </Select.Content>
                            </Select.Root>
                        </>
                    )}
                    {cardUID && (
                        <>
                            <label htmlFor={actionSelectID}>{t("settings.Permission action")}</label>
                            <Select.Root value={action} onValueChange={(value) => setAction(value as "read" | "card_update")}>
                                <Select.Trigger id={actionSelectID}>
                                    <Select.Value />
                                </Select.Trigger>
                                <Select.Content>
                                    <Select.Item value="read">{t("role.project.read")}</Select.Item>
                                    <Select.Item value="card_update">{t("role.project.card_update")}</Select.Item>
                                </Select.Content>
                            </Select.Root>
                        </>
                    )}
                </div>
            )}
            {loading && <p role="status">{t("common.Loading...")}</p>}
            {error && (
                <p role="alert">
                    {t(
                        error === 403
                            ? "settings.Directory forbidden"
                            : error === 404
                              ? "settings.Permission resource unavailable"
                              : "settings.User details failed"
                    )}
                </p>
            )}
            {data && (
                <div aria-live="polite" className="space-y-2 text-sm">
                    <p className="font-medium">
                        {t(
                            data.action === "card.update"
                                ? data.allowed
                                    ? "settings.Card update allowed"
                                    : "settings.Card update denied"
                                : data.resource.type === "card"
                                  ? data.allowed
                                      ? "settings.Card read allowed"
                                      : "settings.Card read denied"
                                  : data.allowed
                                    ? "settings.Board read allowed"
                                    : "settings.Board read denied"
                        )}
                    </p>
                    {!data.allowed && (
                        <p>
                            {t(
                                data.action === "card.update"
                                    ? "settings.Card update denied reason"
                                    : data.resource.type === "card"
                                      ? "settings.Card read denied reason"
                                      : "settings.Board read denied reason"
                            )}
                        </p>
                    )}
                    {!!data.sources.length && (
                        <ul className="list-inside list-disc">
                            {data.sources.map((source) => (
                                <li key={`${source.type}:${source.uid || ""}`}>{t(`settings.Permission source ${source.type}`)}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
            {projectUID && (
                <Button variant="outline" size="sm" disabled={loading} onClick={retry}>
                    {t(error ? "common.Retry" : "common.Refresh")}
                </Button>
            )}
        </section>
    );
}
