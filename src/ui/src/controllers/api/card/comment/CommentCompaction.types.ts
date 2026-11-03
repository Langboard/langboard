export interface CommentCompaction {
    uid: string;
    request_key?: string;
    status: "pending" | "running" | "completed" | "failed" | "cancelled" | "retry_required" | "invalidated";
    source_revision?: string;
    evidence_refs?: string[];
    covered_from?: string;
    covered_through?: string;
    generated_by?: { uid: string; display_name: string };
    draft?: {
        claims: { text: string; kind: "fact" | "reported_success" | "reported_failure" | "unresolved" | "conflict"; evidence_refs: string[] }[];
    };
}

export interface CommentCompactionEligibility {
    can_generate: boolean;
    provider_configured: boolean;
    needs_compaction: boolean;
    next_request_key: string | null;
}
