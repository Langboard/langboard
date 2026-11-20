export interface CustodyReceipt {
    custody_uid: string;
    connection_uid: string;
    revision: number;
    state: string;
    destination_uid: string;
    destination_revision: number;
}
export interface HandoffReceipt {
    handoff_uid: string;
    state: string;
    custody_revision: number;
}
export interface CustodyMetadata {
    connection_uid: string;
    connection_revision: string;
    custody: CustodyReceipt | null;
    current: boolean;
    approved_by_me: boolean;
    destination: { destination_uid: string; revision: number; origin: string } | null;
    last_handoff: HandoffReceipt | null;
}

export function canHandoffCustody(metadata: CustodyMetadata | undefined): boolean {
    if (!metadata?.custody) return false;
    const custody = metadata.custody;
    return !!(
        custody.state === "enabled" &&
        metadata.current &&
        metadata.approved_by_me &&
        metadata.destination?.destination_uid === custody.destination_uid &&
        metadata.destination.revision === custody.destination_revision &&
        metadata.last_handoff?.custody_revision !== custody.revision
    );
}
