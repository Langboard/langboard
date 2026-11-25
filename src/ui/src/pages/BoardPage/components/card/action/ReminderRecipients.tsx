import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";
import { reminderSelectClass } from "./ReminderRecurrenceFields";

export type RecipientOverrides = { include: string[]; exclude: string[] };
export type ReminderMember = { uid: string; label: string };

export default function ReminderRecipients({
    members,
    value,
    onChange,
}: {
    members: ReminderMember[];
    value: RecipientOverrides;
    onChange: (next: RecipientOverrides) => void;
}) {
    const { t } = useTranslation();
    const selected = new Set([...value.include, ...value.exclude]);
    return (
        <details className="rounded-md border px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">{t("card.reminder.recipients")}</summary>
            <div className="mt-2 space-y-3">
                {(["include", "exclude"] as const).map((kind) => (
                    <fieldset className="space-y-2" key={kind}>
                        <legend className="text-sm text-muted-foreground">{t(`card.reminder.recipient.${kind}`)}</legend>
                        {value[kind].length > 0 && (
                            <ul className="flex flex-wrap gap-1">
                                {value[kind].map((uid) => {
                                    const label = members.find((member) => member.uid === uid)?.label ?? t("card.reminder.recipient.unavailable");
                                    return (
                                        <li key={uid} className="flex items-center gap-1 rounded-md border ps-2 text-xs">
                                            {label}
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant="ghost"
                                                aria-label={t("card.reminder.recipient.remove", { name: label })}
                                                onClick={() => onChange({ ...value, [kind]: value[kind].filter((item) => item !== uid) })}
                                            >
                                                ×
                                            </Button>
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                        <select
                            className={reminderSelectClass}
                            aria-label={t(`card.reminder.recipient.${kind}`)}
                            value=""
                            disabled={value[kind].length >= 50}
                            onChange={(event) => {
                                const uid = event.target.value;
                                if (uid) onChange({ ...value, [kind]: [...value[kind], uid] });
                            }}
                        >
                            <option value="">{t("card.reminder.recipient.choose")}</option>
                            {members
                                .filter((member) => !selected.has(member.uid))
                                .map((member) => (
                                    <option key={member.uid} value={member.uid}>
                                        {member.label}
                                    </option>
                                ))}
                        </select>
                    </fieldset>
                ))}
            </div>
        </details>
    );
}
