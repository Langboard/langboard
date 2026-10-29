import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Textarea from "@/components/base/Textarea";
import { ProjectCard } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import useChangeCardDetails from "@/controllers/api/card/useChangeCardDetails";

export function NextActionField({ value, canEdit, save }: { value?: string | null; canEdit: boolean; save: (value: string) => Promise<unknown> }) {
    const [t] = useTranslation();
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(value ?? "");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(false);
    const pending = useRef(false);
    useEffect(() => {
        if (!editing) setDraft(value ?? "");
    }, [value, editing]);
    useEffect(() => {
        if (!canEdit) {
            setEditing(false);
            setError(false);
        }
    }, [canEdit]);
    const submit = async (next: string) => {
        if (!canEdit || pending.current) return;
        pending.current = true;
        setSaving(true);
        setError(false);
        try {
            await save(next);
            setEditing(false);
        } catch {
            setError(true);
        } finally {
            pending.current = false;
            setSaving(false);
        }
    };
    return (
        <section className="w-full min-w-0 space-y-2 text-sm" aria-label={t("card.Next action")}>
            <p className="font-medium">{t("card.Next action")}</p>
            {editing && canEdit ? (
                <form
                    onSubmit={(event) => {
                        event.preventDefault();
                        void submit(draft);
                    }}
                    className="space-y-2"
                >
                    <Textarea
                        className="w-full"
                        aria-label={t("card.Next action")}
                        value={draft}
                        maxLength={1000}
                        disabled={saving}
                        onChange={(event) => setDraft(event.target.value)}
                    />
                    <div className="flex flex-wrap gap-2">
                        <Button type="submit" disabled={saving} aria-busy={saving}>
                            {t("card.Save next action")}
                        </Button>
                        <Button
                            type="button"
                            disabled={saving}
                            onClick={() => {
                                setEditing(false);
                                setError(false);
                            }}
                        >
                            {t("card.Cancel")}
                        </Button>
                        <Button type="button" disabled={saving} onClick={() => void submit("")}>
                            {t("card.Clear next action")}
                        </Button>
                    </div>
                    {error && <p role="alert">{t("card.Could not save next action. Try again.")}</p>}
                </form>
            ) : (
                <>
                    <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{value || t("card.No next action")}</p>
                    {canEdit && (
                        <Button
                            type="button"
                            disabled={saving}
                            onClick={() => {
                                setDraft(value ?? "");
                                setEditing(true);
                            }}
                        >
                            {t("card.Edit next action")}
                        </Button>
                    )}
                </>
            )}
        </section>
    );
}

function EditableNextAction({ card }: { card: ProjectCard.TModel }) {
    const { projectUID, hasRoleAction } = useBoardCard();
    const { mutateAsync } = useChangeCardDetails({ interceptToast: true });
    const value = card.useField("next_action");
    return (
        <NextActionField
            value={value}
            canEdit={!card.source_type && hasRoleAction(ProjectRole.EAction.CardUpdate)}
            save={async (next) => {
                const response = await mutateAsync({ project_uid: projectUID, card_uid: card.uid, next_action: next });
                if (
                    !response ||
                    typeof response !== "object" ||
                    !("next_action" in response) ||
                    (response.next_action !== null && typeof response.next_action !== "string")
                )
                    throw new Error("Invalid next action response");
                card.next_action = response.next_action;
            }}
        />
    );
}

export default function CardNextAction({
    card,
    editable = false,
    compact = false,
}: {
    card: ProjectCard.TModel;
    editable?: boolean;
    compact?: boolean;
}) {
    const [t] = useTranslation();
    const value = card.useField("next_action");
    if (compact)
        return value ? (
            <p
                role="note"
                className="line-clamp-2 min-w-0 whitespace-pre-wrap break-words text-xs [overflow-wrap:anywhere]"
                title={value}
                aria-label={`${t("card.Next action")}: ${value}`}
            >
                {t("card.Next action")}: {value}
            </p>
        ) : null;
    if (editable) return <EditableNextAction key={card.uid} card={card} />;
    return <NextActionField value={value} canEdit={false} save={async () => {}} />;
}
