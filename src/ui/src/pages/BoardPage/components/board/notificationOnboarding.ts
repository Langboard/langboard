/** Browser context transport only. Native proof and explicit consent remain authoritative. */
export const NOTIFICATION_CONNECT_READY = "io.langboard.notification.connect.ready.v1";
export const NOTIFICATION_CONNECT_CONTEXT = "io.langboard.notification.connect.context.v1";

export interface NotificationOnboardingContext {
    handoff_uid: string;
    challenge_uid: string;
    challenge_token: string;
    expires_at: string;
}
export interface NotificationOnboardingMessage extends NotificationOnboardingContext {
    type: typeof NOTIFICATION_CONNECT_CONTEXT;
    receiver_nonce: string;
}

export function notificationContextForReady(
    event: Pick<MessageEvent, "origin" | "source" | "data">,
    popup: WindowProxy,
    expectedOrigin: string,
    context: NotificationOnboardingContext,
    now = Date.now()
): NotificationOnboardingMessage | null {
    try {
        const origin = new URL(expectedOrigin);
        if (
            origin.protocol !== "https:" ||
            origin.origin !== expectedOrigin ||
            event.origin !== expectedOrigin ||
            event.source !== popup ||
            popup.closed
        )
            return null;
        const data: unknown = event.data;
        if (
            !data ||
            typeof data !== "object" ||
            Array.isArray(data) ||
            Object.keys(data).length !== 2 ||
            !("type" in data) ||
            data.type !== NOTIFICATION_CONNECT_READY ||
            !("receiver_nonce" in data) ||
            typeof data.receiver_nonce !== "string" ||
            !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(data.receiver_nonce)
        )
            return null;
        if (
            !/^[A-Za-z0-9]{11}$/.test(context.handoff_uid) ||
            !/^[A-Za-z0-9]{11}$/.test(context.challenge_uid) ||
            !/^[A-Za-z0-9_-]{43}$/.test(context.challenge_token) ||
            !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(context.expires_at)
        )
            return null;
        const expires = Date.parse(context.expires_at);
        if (!Number.isFinite(expires) || !Number.isFinite(now) || expires <= now) return null;
        return {
            type: NOTIFICATION_CONNECT_CONTEXT,
            receiver_nonce: data.receiver_nonce,
            handoff_uid: context.handoff_uid,
            challenge_uid: context.challenge_uid,
            challenge_token: context.challenge_token,
            expires_at: context.expires_at,
        };
    } catch {
        return null;
    }
}
