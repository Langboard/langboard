import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Project, ProjectCard } from "@/core/models";
import { useEffect, useRef, useState } from "react";

const useGetDashboardCards = (limit: number = 30, options?: TMutationOptions) => {
    const { mutate } = useQueryMutation();
    const [isLastPage, setIsLastPage] = useState(true);
    const [cardUIDs, setCardUIDs] = useState<string[]>([]);
    const isFetchingRef = useRef(false);
    const lastCurrentDateRef = useRef(new Date());
    const pageRef = useRef(0);

    const getDashboardCards = async () => {
        if ((isLastPage && pageRef.current) || isFetchingRef.current) {
            return {};
        }

        isFetchingRef.current = true;
        try {
            const nextPage = pageRef.current + 1;

            const res = await api.get(Routing.API.DASHBOARD.CARDS, {
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
            setCardUIDs((prev) => [
                ...prev.filter((uid) => !(res.data.cards as ProjectCard.Interface[]).some((card) => card.uid === uid)),
                ...(res.data.cards as ProjectCard.Interface[]).map((card) => card.uid),
            ]);

            setIsLastPage(res.data.cards.length < limit);

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
            void getDashboardCards().catch(() => {});
        }, 0);

        return () => {
            clearTimeout(initialLoad);
            pageRef.current = 0;
        };
    }, []);

    const result = mutate(["get-dashboard-cards"], getDashboardCards, {
        ...options,
        retry: 0,
    });
    return { ...result, isLastPage, cardUIDs, isFetchingRef };
};

export default useGetDashboardCards;
