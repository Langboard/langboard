import { useMemo } from "react";
import CustomSelect from "@/components/Cron/CustomSelect";
import { UNITS } from "@/components/Cron/constants";
import { WeekDaysProps } from "@/components/Cron/types";
import Flex from "@/components/base/Flex";
import { useTranslation } from "react-i18next";

export default function WeekDays(props: WeekDaysProps) {
    const [t] = useTranslation();
    const { value, setValue, className, humanizeLabels, monthDays, disabled, readOnly, period, periodicityOnDoubleClick, mode, filterOption } = props;
    const optionsList = t("cron.weekDays", { returnObjects: true }) as string[];
    const noMonthDays = period === "week" || !monthDays || monthDays.length === 0;

    const placeholder = useMemo(() => {
        if (noMonthDays) {
            return t("cron.every day of the week");
        }

        return t("cron.day of the week");
    }, [noMonthDays, t]);

    const displayWeekDays =
        period === "week" || !readOnly || (value && value.length > 0) || ((!value || value.length === 0) && (!monthDays || monthDays.length === 0));

    const monthDaysIsDisplayed =
        !readOnly || (monthDays && monthDays.length > 0) || ((!monthDays || monthDays.length === 0) && (!value || value.length === 0));

    return displayWeekDays ? (
        <Flex items="center" gap="2">
            {(period === "week" || !monthDaysIsDisplayed) && <span>{t("cron.on")}</span>}

            {period !== "week" && monthDaysIsDisplayed && <span>{t("cron.and")}</span>}

            <CustomSelect
                placeholder={placeholder}
                optionsList={optionsList}
                value={value}
                unit={{
                    ...UNITS[4],
                    alt: t("cron.altWeekDays", { returnObjects: true }) as string[],
                }}
                setValue={setValue}
                className={className}
                humanizeLabels={humanizeLabels}
                disabled={disabled}
                readOnly={readOnly}
                period={period}
                periodicityOnDoubleClick={periodicityOnDoubleClick}
                mode={mode}
                filterOption={filterOption}
            />
        </Flex>
    ) : null;
}
