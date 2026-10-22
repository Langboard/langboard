import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Dialog from "@/components/base/Dialog";
import Input from "@/components/base/Input";
import CreateProjectFormDialog from "./CreateProjectFormDialog";
import type { ProjectCollection } from "./useProjectCollections";

export interface CollectionBoardChoice {
    uid: string;
    title: string;
}
export default function ProjectCollectionDialog({
    opened,
    onOpenChange,
    boards,
    collections,
    save,
    refreshBoards,
}: {
    opened: boolean;
    onOpenChange: (opened: boolean) => void;
    boards: CollectionBoardChoice[];
    collections: ProjectCollection[];
    save: (items: ProjectCollection[]) => Promise<unknown>;
    refreshBoards: () => void;
}) {
    const [t] = useTranslation();
    const nameID = useId();
    const [uid] = useState(() => crypto.randomUUID());
    const [name, setName] = useState("");
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState<string[]>([]);
    const [created, setCreated] = useState<string[]>([]);
    const [creating, setCreating] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(false);
    const choices = [
        ...boards,
        ...created.filter((uid) => !boards.some((board) => board.uid === uid)).map((uid) => ({ uid, title: t("dashboard.New source board") })),
    ];
    const filtered = choices.filter((board) => board.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (saving || !name.trim() || !selected.length) return;
        setSaving(true);
        setError(false);
        try {
            // A retry replaces this draft ID; it does not append a second group.
            await save([...collections.filter((item) => item.uid !== uid), { uid, name: name.trim(), board_uids: selected }]);
            onOpenChange(false);
        } catch {
            setError(true);
        } finally {
            setSaving(false);
        }
    };
    return (
        <>
            <Dialog.Root
                open={opened}
                onOpenChange={(value) => {
                    if (!saving && !creating) onOpenChange(value);
                }}
            >
                <Dialog.Content className="sm:max-w-md" aria-describedby="">
                    <Dialog.Header>
                        <Dialog.Title>{t("dashboard.Add project collection")}</Dialog.Title>
                    </Dialog.Header>
                    <form onSubmit={submit} className="space-y-4">
                        <div className="space-y-2">
                            <label htmlFor={nameID}>{t("dashboard.Collection name")}</label>
                            <Input
                                id={nameID}
                                value={name}
                                onChange={(event) => setName(event.currentTarget.value)}
                                maxLength={100}
                                required
                                autoFocus
                                disabled={saving}
                            />
                        </div>
                        <fieldset disabled={saving} className="min-w-0 space-y-2">
                            <legend>{t("dashboard.Source boards")}</legend>
                            <Input
                                aria-label={t("dashboard.Search source boards")}
                                value={query}
                                onChange={(event) => setQuery(event.currentTarget.value)}
                                clearable
                            />
                            <div className="max-h-64 space-y-3 overflow-y-auto rounded-md border p-3">
                                {filtered.map((board) => (
                                    <Checkbox
                                        key={board.uid}
                                        label={board.title}
                                        checked={selected.includes(board.uid)}
                                        onCheckedChange={(checked) =>
                                            setSelected((items) =>
                                                checked ? [...new Set([...items, board.uid])] : items.filter((uid) => uid !== board.uid)
                                            )
                                        }
                                    />
                                ))}
                                {!filtered.length && <p className="text-sm text-muted-foreground">{t("dashboard.No source boards")}</p>}
                            </div>
                            <Button type="button" variant="outline" onClick={() => setCreating(true)}>
                                {t("dashboard.Create source board")}
                            </Button>
                            {created.length > 0 && (
                                <p className="text-xs text-muted-foreground">{t("dashboard.Created boards are kept if you cancel")}</p>
                            )}
                        </fieldset>
                        {error && (
                            <p role="alert" className="text-sm text-destructive">
                                {t("dashboard.Could not save collection")}
                            </p>
                        )}
                        <Dialog.Footer>
                            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)} disabled={saving}>
                                {t("common.Cancel")}
                            </Button>
                            <Button type="submit" disabled={saving || !name.trim() || !selected.length}>
                                {t("common.Save")}
                            </Button>
                        </Dialog.Footer>
                    </form>
                </Dialog.Content>
            </Dialog.Root>
            <CreateProjectFormDialog
                opened={opened && creating}
                setOpened={setCreating}
                onCreated={(uid) => {
                    setCreated((items) => [...new Set([...items, uid])]);
                    setSelected((items) => [...new Set([...items, uid])]);
                    refreshBoards();
                }}
            />
        </>
    );
}
