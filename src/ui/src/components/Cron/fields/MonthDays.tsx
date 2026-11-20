import { useMemo } from "react";
import CustomSelect from "@/components/Cron/CustomSelect";
import { UNITS } from "@/components/Cron/constants";
import { MonthDaysProps } from "@/components/Cron/types";
import Flex from "@/components/base/Flex";
import { useTranslation } from "react-i18next";

function MonthDays(props: MonthDaysProps) {
    const [t] = useTranslation();
    const { value, setValue, className, weekDays, disabled, readOnly, leadingZero, period, periodicityOnDoubleClick, mode, filterOption } = props;
    const noWeekDays = !weekDays || weekDays.length === 0;
    const placeholder = useMemo(() => {
        if (noWeekDays) {
            return t("cron.every day of the month");
        }

        return t("cron.day of the month");
    }, [noWeekDays, t]);
    const altMonthDays: string[] = [];
    const displayMonthDays = !readOnly || (value && value.length > 0) || ((!value || value.length === 0) && (!weekDays || weekDays.length === 0));

    for (let i = 1; i <= 31; ++i) {
        if (i === 1 || i === 21 || i === 31) {
            altMonthDays.push(t("cron.{day}st", { day: i }));
        } else if (i === 2 || i === 22) {
            altMonthDays.push(t("cron.{day}nd", { day: i }));
        } else if (i === 3 || i === 23) {
            altMonthDays.push(t("cron.{day}rd", { day: i }));
        } else {
            altMonthDays.push(t("cron.{day}th", { day: i }));
        }
    }

    return displayMonthDays ? (
        <Flex items="center" gap="2">
            <span>{t("cron.on")}</span>

            <CustomSelect
                placeholder={placeholder}
                value={value}
                setValue={setValue}
                unit={{
                    ...UNITS[2],
                    alt: altMonthDays,
                }}
                className={className}
                disabled={disabled}
                readOnly={readOnly}
                leadingZero={leadingZero}
                period={period}
                periodicityOnDoubleClick={periodicityOnDoubleClick}
                mode={mode}
                filterOption={filterOption}
            />
        </Flex>
    ) : null;
}

export default MonthDays;
