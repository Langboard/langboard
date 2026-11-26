import Button from "@/components/base/Button";
import DropdownMenu from "@/components/base/DropdownMenu";
import IconComponent from "@/components/base/IconComponent";
import { reminderSnoozeMinutes } from "@/controllers/api/notification/useNotificationInbox";
import { useTranslation } from "react-i18next";

interface ReminderSnoozeMenuProps {
    className?: string;
    disabled: boolean;
    onSnooze: (minutes: (typeof reminderSnoozeMinutes)[number]) => void;
}

export default function ReminderSnoozeMenu({ className, disabled, onSnooze }: ReminderSnoozeMenuProps) {
    const [t] = useTranslation();
    return (
        <DropdownMenu.Root modal={false}>
            <DropdownMenu.Trigger asChild>
                <Button type="button" variant="ghost" size="sm" className={className} disabled={disabled}>
                    <IconComponent icon="clock" className="size-4" />
                    {t("notification.inbox.snooze")}
                </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content align="end">
                {reminderSnoozeMinutes.map((minutes) => (
                    <DropdownMenu.Item key={minutes} onSelect={() => onSnooze(minutes)}>
                        {t(`notification.inbox.snoozeFor.${minutes}`)}
                    </DropdownMenu.Item>
                ))}
            </DropdownMenu.Content>
        </DropdownMenu.Root>
    );
}
