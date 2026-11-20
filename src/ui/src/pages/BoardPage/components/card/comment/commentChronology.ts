interface CommentTimestamp {
    uid: string;
    created_at: Date;
}

/** Edits retain their event position; identical timestamps have a stable tie-break. */
export function newestCommentsFirst<T extends CommentTimestamp>(comments: readonly T[]): T[] {
    return [...comments].sort((a, b) => b.created_at.getTime() - a.created_at.getTime() || (a.uid < b.uid ? 1 : a.uid > b.uid ? -1 : 0));
}
