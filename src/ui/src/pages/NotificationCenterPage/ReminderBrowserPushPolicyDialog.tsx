import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import { reminderBrowserPushSettingsPath, type ReminderBrowserPushPolicy } from "@/controllers/api/notification/useReminderBrowserPushSettings";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

const timeValue = (minutes: number) => [Math.floor(minutes / 60), minutes % 60].map((value) => value.toString().padStart(2, "0")).join(":");

function toMinutes(value: string) {
    const [hours, minutes] = value.split(":").map(Number);
    return hours * 60 + minutes;
}

export default function ReminderBrowserPushPolicyDialog({ policy, onSaved }: { policy: ReminderBrowserPushPolicy; onSaved: () => Promise<void> }) {
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const save = useMutation({
        retry: false,
        mutationFn: async (changes: ReminderBrowserPushPolicy) => {
            const auth = useAuthStore.getState();
            if (!actorUID || auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session) {
                throw new Error("Browser push session changed");
            }
            await api.put(`${reminderBrowserPushSettingsPath}/policy`, changes);
        },
        onSuccess: async () => {
            await onSaved();
            setOpen(false);
        },
    });
    return (
        <Dialog.Root
            open={open}
            onOpenChange={(next) => {
                if (save.isPending) return;
                save.reset();
                setOpen(next);
            }}
        >
            <Dialog.Trigger asChild>
                <Button variant="ghost" size="sm">
                    {t("notification.webPush.settings")}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="max-w-md">
                <Dialog.Title>{t("notification.webPush.settings")}</Dialog.Title>
                <Dialog.Description className="sr-only">{t("notification.webPush.description")}</Dialog.Description>
                {open && <PolicyForm policy={policy} saving={save.isPending} error={save.isError} onSave={save.mutate} />}
            </Dialog.Content>
        </Dialog.Root>
    );
}

function PolicyForm({
    policy,
    saving,
    error,
    onSave,
}: {
    policy: ReminderBrowserPushPolicy;
    saving: boolean;
    error: boolean;
    onSave: (policy: ReminderBrowserPushPolicy) => void;
}) {
    const [t] = useTranslation();
    const [quiet, setQuiet] = useState(!!policy.quiet_hours);
    const [start, setStart] = useState(timeValue(policy.quiet_hours?.start_minute ?? 22 * 60));
    const [end, setEnd] = useState(timeValue(policy.quiet_hours?.end_minute ?? 7 * 60));
    const [timezone, setTimezone] = useState(policy.quiet_hours?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
    const [blockUrgent, setBlockUrgent] = useState(policy.block_urgent);
    const [preview, setPreview] = useState(policy.show_preview);
    const equalTimes = quiet && start === end;

    return (
        <form
            className="space-y-3"
            onSubmit={(event) => {
                event.preventDefault();
                if (equalTimes) return;
                onSave({
                    quiet_hours: quiet ? { timezone, start_minute: toMinutes(start), end_minute: toMinutes(end) } : null,
                    block_urgent: blockUrgent,
                    show_preview: preview,
                });
            }}
        >
            <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="checkbox size-4" checked={quiet} onChange={(event) => setQuiet(event.target.checked)} />
                {t("notification.webPush.quiet")}
            </label>
            {quiet && (
                <div className="grid grid-cols-2 gap-2 text-sm">
                    <label className="grid gap-1">
                        {t("notification.webPush.start")}
                        <input type="time" className="input w-full" required value={start} onChange={(event) => setStart(event.target.value)} />
                    </label>
                    <label className="grid gap-1">
                        {t("notification.webPush.end")}
                        <input type="time" className="input w-full" required value={end} onChange={(event) => setEnd(event.target.value)} />
                    </label>
                    <label className="col-span-2 grid gap-1">
                        {t("notification.webPush.timezone")}
                        <select className="select w-full" value={timezone} onChange={(event) => setTimezone(event.target.value)}>
                            {[...new Set([timezone, "UTC", ...(Intl.supportedValuesOf?.("timeZone") ?? [])])].map((zone) => (
                                <option key={zone}>{zone}</option>
                            ))}
                        </select>
                    </label>
                </div>
            )}
            <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="checkbox size-4" checked={blockUrgent} onChange={(event) => setBlockUrgent(event.target.checked)} />
                {t("notification.webPush.blockUrgent")}
            </label>
            <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="checkbox size-4" checked={preview} onChange={(event) => setPreview(event.target.checked)} />
                {t("notification.webPush.preview")}
            </label>
            {(equalTimes || error) && (
                <p role="alert" className="text-sm text-destructive">
                    {t(equalTimes ? "notification.webPush.invalidWindow" : "notification.webPush.error")}
                </p>
            )}
            <Button type="submit" disabled={saving || equalTimes}>
                {t("common.Save")}
            </Button>
        </form>
    );
}
