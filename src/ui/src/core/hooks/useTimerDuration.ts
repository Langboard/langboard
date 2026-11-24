import { add as addDate, differenceInSeconds, intervalToDuration } from "date-fns";
import { useEffect, useReducer } from "react";

/** Follow live timer fields; stopped and paused views schedule no timer work. */
export function useTimerDuration(running: boolean, accumulatedSeconds: number, startedAt: Date | null | undefined) {
    const [, forceUpdate] = useReducer((value) => value + 1, 0);
    useEffect(() => {
        if (!running || !startedAt) return;
        const interval = setInterval(forceUpdate, 1000);
        return () => clearInterval(interval);
    }, [running, startedAt]);
    const now = new Date();
    const seconds = accumulatedSeconds + (running && startedAt ? differenceInSeconds(now, startedAt) : 0);
    return intervalToDuration({ start: now, end: addDate(now, { seconds }) });
}
