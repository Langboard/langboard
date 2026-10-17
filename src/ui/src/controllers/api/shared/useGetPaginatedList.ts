/* eslint-disable @typescript-eslint/no-explicit-any */
import { TGetListForm } from "@/controllers/api/shared/types";
import { getListRequestData } from "@/controllers/api/shared/utils";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { BaseModel } from "@/core/models/Base";
import { TCreatedAtModel, TCreatedAtModelName } from "@/core/models/ModelRegistry";
import { useCallback, useRef, useState } from "react";

export interface IUseGetPaginatedListProps<TModelName extends TCreatedAtModelName> {
    form: TGetListForm<TModelName>;
    limit: number;
    lastCurrentDateRef: React.RefObject<Date>;
    prepareData?: (
        records: Omit<TCreatedAtModel<IUseGetPaginatedListProps<TModelName>["form"]["listType"]>, keyof Omit<BaseModel<any>, "uid">>[],
        data: any
    ) => void;
}

const useGetPaginatedList = <TModelName extends TCreatedAtModelName>(
    { form, limit, lastCurrentDateRef, prepareData }: IUseGetPaginatedListProps<TModelName>,
    options?: TMutationOptions
) => {
    const { mutate } = useQueryMutation();
    const [countNewRecords, setCountNewRecords] = useState(0);
    const isFetchingRef = useRef(false);
    const limitRef = useRef(limit);

    const [model, url] = getListRequestData(form);

    const getPaginatedList = useCallback(async () => {
        if (isFetchingRef.current) {
            return {};
        }

        isFetchingRef.current = true;
        try {
            const res = await api.get(url, {
                params: {
                    assignee_uid: (form as any).assignee_uid,
                    refer_time: lastCurrentDateRef.current,
                    limit: limitRef.current,
                },
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            });

            if (res.data.records) {
                prepareData?.(res.data.records, res.data);
            }

            model.fromArray(res.data.records, true);

            if (res.data.count_new_records) {
                setCountNewRecords(res.data.count_new_records);
            }

            return {};
        } finally {
            isFetchingRef.current = false;
        }
    }, [countNewRecords, setCountNewRecords]);

    const refresh = useCallback(async () => {
        if (isFetchingRef.current || !countNewRecords) {
            return;
        }

        const curLimit = limitRef.current;
        const previousDate = lastCurrentDateRef.current;
        limitRef.current = countNewRecords;
        lastCurrentDateRef.current = new Date();

        try {
            await getPaginatedList();
            setCountNewRecords(0);
        } catch (error) {
            lastCurrentDateRef.current = previousDate;
            throw error;
        } finally {
            limitRef.current = curLimit;
        }
    }, [getPaginatedList, countNewRecords, setCountNewRecords]);

    const checkOutdated = useCallback(
        async (referTime: Date) => {
            if (isFetchingRef.current) {
                return;
            }

            const res = await api.get(url, {
                params: {
                    only_count: true,
                    refer_time: referTime,
                    page: 1,
                    limit: 1,
                },
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            });

            if (res.data.count_new_records) {
                setCountNewRecords(res.data.count_new_records);
            }
        },
        [countNewRecords, setCountNewRecords]
    );

    const result = mutate([`get-paginated-list-${url.replace(/\//g, "-")}`], getPaginatedList, {
        ...options,
        retry: 0,
    });

    return { ...result, countNewRecords, refresh, checkOutdated };
};

export default useGetPaginatedList;
