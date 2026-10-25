import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Project, ProjectCard, ProjectCheckitem } from "@/core/models";
import { useEffect, useRef, useState } from "react";

const useGetTrackingList = (limit: number = 30, options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();
    const [isLastPage, setIsLastPage] = useState(true);
    const [checkitemUIDs, setCheckitemUIDs] = useState<string[]>([]);
    const isFetchingRef = useRef(false);
    const lastCurrentDateRef = useRef(new Date());
    const pageRef = useRef(0);
    const mountedRef = useRef(false);

    const getTrackingList = async () => {
        if ((isLastPage && pageRef.current) || isFetchingRef.current) {
            return {};
        }

        isFetchingRef.current = true;
        try {
            const nextPage = pageRef.current + 1;

            const res = await api.get(Routing.API.DASHBOARD.TRACKING, {
                params: {
                    refer_time: lastCurrentDateRef.current,
                    page: nextPage,
                    limit,
                },
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            });

            if (!mountedRef.current) return {};

            Project.Model.fromArray(res.data.projects, true);
            ProjectCard.Model.fromArray(res.data.cards, true);
            ProjectCheckitem.Model.fromArray(res.data.checkitems, true);
            setCheckitemUIDs((prev) => [
                ...prev.filter((uid) => !(res.data.checkitems as ProjectCheckitem.Interface[]).some((item) => item.uid === uid)),
                ...(res.data.checkitems as ProjectCheckitem.Interface[]).map((item) => item.uid),
            ]);

            setIsLastPage(res.data.checkitems.length < limit);

            pageRef.current = nextPage;
            return {};
        } finally {
            isFetchingRef.current = false;
        }
    };

    const result = mutate(["get-dashboard-tracking-list"], getTrackingList, {
        ...options,
        retry: 0,
    });
    const { mutateAsync } = result;

    useEffect(() => {
        mountedRef.current = true;
        lastCurrentDateRef.current = new Date();
        const initialLoad = setTimeout(() => {
            // Initial and subsequent requests share observable mutation state.
            void mutateAsync({}).catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            mountedRef.current = false;
        };
    }, [mutateAsync]);

    return { ...result, isLastPage, checkitemUIDs, isFetchingRef };
};

export default useGetTrackingList;
