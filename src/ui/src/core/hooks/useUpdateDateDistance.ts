import { formatDateDistance } from "@/core/utils/LocaleFormat";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const useUpdateDateDistance = (date: Date | undefined, timeout: number = 60000) => {
    const [, i18n] = useTranslation();
    const [distance, setDistance] = useState(date ? formatDateDistance(date, i18n.language) : "");

    useEffect(() => {
        let runningTimeout: NodeJS.Timeout | undefined;
        const updateCommentedAt = () => {
            if (runningTimeout) {
                clearTimeout(runningTimeout);
                runningTimeout = undefined;
            }

            setDistance(date ? formatDateDistance(date, i18n.language) : "");

            runningTimeout = setTimeout(updateCommentedAt, timeout);
        };

        updateCommentedAt();

        return () => {
            clearTimeout(runningTimeout);
            runningTimeout = undefined;
        };
    }, [date, i18n, i18n.language, timeout]);

    return distance;
};

export default useUpdateDateDistance;
