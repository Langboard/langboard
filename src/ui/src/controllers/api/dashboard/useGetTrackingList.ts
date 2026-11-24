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

    useEffect(() => {
        if (pageRef.current) {
            return;
        }

        lastCurrentDateRef.current = new Date();
        const initialLoad = setTimeout(() => {
            void getTrackingList().catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            pageRef.current = 0;
        };
    }, []);

    const result = mutate(["get-dashboard-tracking-list"], getTrackingList, {
        ...options,
        retry: 0,
    });
    return { ...result, isLastPage, checkitemUIDs, isFetchingRef };
};

export default useGetTrackingList;
