import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";

export interface ICardReader {
    user_uid: string;
    seen_at: string;
}
export const cardReadStateKey = (projectUID: string, cardUID: string) => ["card-read-state", projectUID, cardUID];

export default function useCardReadState(projectUID: string, cardUID: string, version = 0) {
    const { query } = useQueryMutation();
    const path = `/board/${encodeURIComponent(projectUID)}/card/${encodeURIComponent(cardUID)}`;
    const readers = query(
        [...cardReadStateKey(projectUID, cardUID), version],
        async () => {
            const response = await api.get<{ readers: ICardReader[] }>(`${path}/read-state`);
            return response.data.readers;
        },
        { staleTime: 10000, retry: 0, refetchInterval: 30000 }
    );
    return { readers };
}
