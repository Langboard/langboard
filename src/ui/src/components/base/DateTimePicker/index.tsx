/**
 * Shadcn Datetime Picker with support for timezone, date and time selection, minimum and maximum date limits, and 12-hour format...
 * Check out the live demo at https://shadcn-datetime-picker-pro.vercel.app/
 * Find the latest source code at https://github.com/huybuidac/shadcn-datetime-picker
 */
"use client";

import * as React from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Popover from "@/components/base/Popover";
import Calendar, { ICalendarProps } from "@/components/base/Calendar";
import { ScreenMap } from "@/core/utils/VariantUtils";
import Dialog from "@/components/base/Dialog";
import { measureComponentHeight } from "@/core/utils/ComponentUtils";

export interface IDateTimePickerProps extends ICalendarProps {
    renderTrigger: (props: DateTimeRenderTriggerProps) => React.ReactNode;
}

export type DateTimeRenderTriggerProps = {
    open: bool;
    timezone?: string;
    disabled?: bool;
    use12HourFormat?: bool;
    setOpen: (open: bool) => void;
};

function DateTimePicker({ renderTrigger, onChange, ...props }: IDateTimePickerProps): React.JSX.Element {
    const { timezone, disabled, use12HourFormat, hideTime } = props;
    const [t] = useTranslation();
    const title = t(hideTime ? "date.Choose date" : "date.Choose date and time");
    const [open, setOpen] = useState(false);
    const [isDialog, setIsDialog] = useState(false);
    const triggerRef = React.useRef<HTMLButtonElement>(null);
    const handleChange = (date: Date | undefined) => {
        onChange(date);
        setOpen(false);
    };
    const checkDialogHeight = React.useCallback(async () => {
        if (!triggerRef.current) {
            return;
        }

        const rect = triggerRef.current.getBoundingClientRect();

        const height = await measureComponentHeight(<Calendar onChange={() => {}} {...props} />);
        setIsDialog(window.innerWidth < ScreenMap.size.sm || window.innerHeight < height + rect.bottom);
    }, []);

    React.useEffect(() => {
        checkDialogHeight();

        let resizeTimeout: NodeJS.Timeout;
        const resizeHandler = () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                checkDialogHeight();
            }, 100);
        };

        window.addEventListener("resize", resizeHandler);

        return () => {
            window.removeEventListener("resize", resizeHandler);
        };
    }, []);

    const caledar = <Calendar onChange={handleChange} {...props} />;

    return (
        <>
            {isDialog ? (
                <Dialog.Root open={open} onOpenChange={setOpen}>
                    <Dialog.Trigger asChild ref={triggerRef}>
                        {renderTrigger({ open, timezone, disabled, use12HourFormat, setOpen })}
                    </Dialog.Trigger>
                    <Dialog.Content className="w-auto p-2 pt-8" aria-describedby="">
                        <Dialog.Title className="sr-only">{title}</Dialog.Title>
                        {caledar}
                    </Dialog.Content>
                </Dialog.Root>
            ) : (
                <Popover.Root modal open={open} onOpenChange={setOpen}>
                    <Popover.Trigger asChild ref={triggerRef}>
                        {renderTrigger({ open, timezone, disabled, use12HourFormat, setOpen })}
                    </Popover.Trigger>
                    <Popover.Content className="w-auto p-2" aria-label={title}>
                        {caledar}
                    </Popover.Content>
                </Popover.Root>
            )}
        </>
    );
}

export default DateTimePicker;
