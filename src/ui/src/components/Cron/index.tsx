import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCronStringFromValues, setValuesFromCronString } from "@/components/Cron/converter";
import Hours from "@/components/Cron/fields/Hours";
import Minutes from "@/components/Cron/fields/Minutes";
import MonthDays from "@/components/Cron/fields/MonthDays";
import Months from "@/components/Cron/fields/Months";
import Period from "@/components/Cron/fields/Period";
import WeekDays from "@/components/Cron/fields/WeekDays";
import { CronProps, PeriodType } from "@/components/Cron/types";
import { setError, usePrevious } from "@/components/Cron/utils";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";

function Cron(props: CronProps) {
    const [t] = useTranslation();
    const {
        value = "",
        setValue,
        onError,
        className,
        defaultPeriod = "day",
        allowEmpty = "for-default-value",
        humanizeLabels = true,
        humanizeValue = false,
        disabled = false,
        readOnly = false,
        leadingZero = false,
        shortcuts = ["@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"],
        clockFormat,
        periodicityOnDoubleClick = true,
        mode = "multiple",
        allowedDropdowns = ["period", "months", "month-days", "week-days", "hours", "minutes"],
        allowedPeriods = ["year", "month", "week", "day", "hour", "minute", "reboot"],
        dropdownsConfig,
        useClearButton = true,
    } = props;
    const internalValueRef = useRef<string>(value);
    const defaultPeriodRef = useRef<PeriodType>(defaultPeriod);
    const isApplyingExternalValueRef = useRef(false);
    const externalValueTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [period, setPeriod] = useState<PeriodType | undefined>();
    const [monthDays, setMonthDays] = useState<number[] | undefined>();
    const [months, setMonths] = useState<number[] | undefined>();
    const [weekDays, setWeekDays] = useState<number[] | undefined>();
    const [hours, setHours] = useState<number[] | undefined>();
    const [minutes, setMinutes] = useState<number[] | undefined>();
    const [valueCleared, setValueCleared] = useState<bool>(false);
    const previousValueCleared = usePrevious(valueCleared);

    useEffect(() => {
        isApplyingExternalValueRef.current = true;
        if (externalValueTimerRef.current) {
            clearTimeout(externalValueTimerRef.current);
        }

        setValuesFromCronString(
            value,
            onError,
            allowEmpty,
            internalValueRef,
            true,
            shortcuts,
            setMinutes,
            setHours,
            setMonthDays,
            setMonths,
            setWeekDays,
            setPeriod,
            t
        );

        externalValueTimerRef.current = setTimeout(() => {
            isApplyingExternalValueRef.current = false;
            externalValueTimerRef.current = null;
        }, 0);
    }, [value]);

    useEffect(() => {
        return () => {
            if (externalValueTimerRef.current) {
                clearTimeout(externalValueTimerRef.current);
            }
        };
    }, []);

    useEffect(() => {
        if (isApplyingExternalValueRef.current) {
            return;
        }

        // Only change the value if a user touched a field
        // and if the user didn't use the clear button
        if ((period || minutes || months || monthDays || weekDays || hours) && !valueCleared && !previousValueCleared) {
            const selectedPeriod = period || defaultPeriodRef.current;
            const cron = getCronStringFromValues(selectedPeriod, months, monthDays, weekDays, hours, minutes, humanizeValue, dropdownsConfig);

            setValue(cron, { selectedPeriod });
            internalValueRef.current = cron;

            if (onError) {
                onError(undefined);
            }
        } else if (valueCleared) {
            setValueCleared(false);
        }
    }, [period, monthDays, months, weekDays, hours, minutes, humanizeValue, valueCleared, dropdownsConfig]);

    const handleClear = useCallback(() => {
        setMonthDays(undefined);
        setMonths(undefined);
        setWeekDays(undefined);
        setHours(undefined);
        setMinutes(undefined);

        let newValue = "";

        const newPeriod = period !== "reboot" && period ? period : defaultPeriodRef.current;

        if (newPeriod !== period) {
            setPeriod(newPeriod);
        }

        newValue = getCronStringFromValues(newPeriod, undefined, undefined, undefined, undefined, undefined, undefined, undefined);

        setValue(newValue, { selectedPeriod: newPeriod });
        internalValueRef.current = newValue;

        setValueCleared(true);

        if (allowEmpty === "never") {
            setError(onError, t);
        } else {
            if (onError) {
                onError(undefined);
            }
        }
    }, [period, setValue, onError]);

    const clearButtonNode = useMemo(() => {
        if (useClearButton && !readOnly) {
            return (
                <Button variant="destructive" disabled={disabled} onClick={handleClear}>
                    {t("common.Clear")}
                </Button>
            );
        }

        return null;
    }, [useClearButton, readOnly, disabled, handleClear]);

    const periodForRender = period || defaultPeriodRef.current;

    return (
        <Flex items="center" gap="2" wrap className={className}>
            {allowedDropdowns.includes("period") && (
                <Period
                    value={periodForRender}
                    setValue={setPeriod}
                    className={className}
                    disabled={dropdownsConfig?.period?.disabled ?? disabled}
                    readOnly={dropdownsConfig?.period?.readOnly ?? readOnly}
                    shortcuts={shortcuts}
                    allowedPeriods={allowedPeriods}
                />
            )}

            {periodForRender === "reboot" ? (
                clearButtonNode
            ) : (
                <>
                    {periodForRender === "year" && allowedDropdowns.includes("months") && (
                        <Months
                            value={months}
                            setValue={setMonths}
                            className={className}
                            humanizeLabels={dropdownsConfig?.months?.humanizeLabels ?? humanizeLabels}
                            disabled={dropdownsConfig?.months?.disabled ?? disabled}
                            readOnly={dropdownsConfig?.months?.readOnly ?? readOnly}
                            period={periodForRender}
                            periodicityOnDoubleClick={dropdownsConfig?.months?.periodicityOnDoubleClick ?? periodicityOnDoubleClick}
                            mode={dropdownsConfig?.months?.mode ?? mode}
                            filterOption={dropdownsConfig?.months?.filterOption}
                        />
                    )}

                    {(periodForRender === "year" || periodForRender === "month") && allowedDropdowns.includes("month-days") && (
                        <MonthDays
                            value={monthDays}
                            setValue={setMonthDays}
                            className={className}
                            weekDays={weekDays}
                            disabled={dropdownsConfig?.["month-days"]?.disabled ?? disabled}
                            readOnly={dropdownsConfig?.["month-days"]?.readOnly ?? readOnly}
                            leadingZero={dropdownsConfig?.["month-days"]?.leadingZero ?? leadingZero}
                            period={periodForRender}
                            periodicityOnDoubleClick={dropdownsConfig?.["month-days"]?.periodicityOnDoubleClick ?? periodicityOnDoubleClick}
                            mode={dropdownsConfig?.["month-days"]?.mode ?? mode}
                            filterOption={dropdownsConfig?.["month-days"]?.filterOption}
                        />
                    )}

                    {(periodForRender === "year" || periodForRender === "month" || periodForRender === "week") &&
                        allowedDropdowns.includes("week-days") && (
                            <WeekDays
                                value={weekDays}
                                setValue={setWeekDays}
                                className={className}
                                humanizeLabels={dropdownsConfig?.["week-days"]?.humanizeLabels ?? humanizeLabels}
                                monthDays={monthDays}
                                disabled={dropdownsConfig?.["week-days"]?.disabled ?? disabled}
                                readOnly={dropdownsConfig?.["week-days"]?.readOnly ?? readOnly}
                                period={periodForRender}
                                periodicityOnDoubleClick={dropdownsConfig?.["week-days"]?.periodicityOnDoubleClick ?? periodicityOnDoubleClick}
                                mode={dropdownsConfig?.["week-days"]?.mode ?? mode}
                                filterOption={dropdownsConfig?.["week-days"]?.filterOption}
                            />
                        )}

                    <Flex items="center" gap="2">
                        {periodForRender !== "minute" && periodForRender !== "hour" && allowedDropdowns.includes("hours") && (
                            <Hours
                                value={hours}
                                setValue={setHours}
                                className={className}
                                disabled={dropdownsConfig?.hours?.disabled ?? disabled}
                                readOnly={dropdownsConfig?.hours?.readOnly ?? readOnly}
                                leadingZero={dropdownsConfig?.hours?.leadingZero ?? leadingZero}
                                clockFormat={clockFormat}
                                period={periodForRender}
                                periodicityOnDoubleClick={dropdownsConfig?.hours?.periodicityOnDoubleClick ?? periodicityOnDoubleClick}
                                mode={dropdownsConfig?.hours?.mode ?? mode}
                                filterOption={dropdownsConfig?.hours?.filterOption}
                            />
                        )}

                        {periodForRender !== "minute" && allowedDropdowns.includes("minutes") && (
                            <Minutes
                                value={minutes}
                                setValue={setMinutes}
                                period={periodForRender}
                                className={className}
                                disabled={dropdownsConfig?.minutes?.disabled ?? disabled}
                                readOnly={dropdownsConfig?.minutes?.readOnly ?? readOnly}
                                leadingZero={dropdownsConfig?.minutes?.leadingZero ?? leadingZero}
                                clockFormat={clockFormat}
                                periodicityOnDoubleClick={dropdownsConfig?.minutes?.periodicityOnDoubleClick ?? periodicityOnDoubleClick}
                                mode={dropdownsConfig?.minutes?.mode ?? mode}
                                filterOption={dropdownsConfig?.minutes?.filterOption}
                            />
                        )}

                        {clearButtonNode}
                    </Flex>
                </>
            )}
        </Flex>
    );
}

export interface ICronTextProps {
    value: string;
    className?: string;
}

export function CronText({ value, className }: ICronTextProps) {
    return <Cron value={value} setValue={() => {}} disabled readOnly className={className} />;
}

export default Cron;
