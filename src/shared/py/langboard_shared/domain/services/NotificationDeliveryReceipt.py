"""Pure receipt transition rules; storage and current authority belong to the caller.

A transport response is deliberately absent from these states. An adapter receipt
does not start work, prove user consent, or grant permission to send a notification.
"""

import re
from dataclasses import dataclass, replace


class NotificationReceiptConflict(ValueError):
    """An immutable event fence or receipt transition does not match."""


STATUSES = frozenset({"adapter_accepted", "confirmed", "unknown", "rejected"})
REASONS = frozenset({"provider_rejected", "provider_outcome_unknown", "destination_unavailable", "expired"})


@dataclass(frozen=True)
class NotificationDeliveryFence:
    event_uid: str
    event_revision: int
    subscription_generation: int
    destination_revision: int

    def __post_init__(self):
        if not isinstance(self.event_uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", self.event_uid):
            raise ValueError("Invalid notification event identity")
        for value in (self.event_revision, self.subscription_generation, self.destination_revision):
            if type(value) is not int or value < 1:
                raise ValueError("Invalid notification delivery fence")


@dataclass(frozen=True)
class NotificationDeliveryReceipt:
    fence: NotificationDeliveryFence
    status: str
    provider_message_id: str | None = None
    reason_code: str | None = None
    revision: int = 1

    def __post_init__(self):
        if not isinstance(self.fence, NotificationDeliveryFence):
            raise ValueError("Notification delivery fence is required")
        if not isinstance(self.status, str) or self.status not in STATUSES:
            raise ValueError("Invalid notification receipt status")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Invalid notification receipt revision")
        message_id = self.provider_message_id
        if message_id is not None and (
            not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in message_id)
        ):
            raise ValueError("Invalid provider message identity")
        if self.status == "confirmed" and message_id is None:
            raise ValueError("Confirmed delivery requires a provider message identity")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or self.reason_code not in REASONS
        ):
            raise ValueError("Invalid notification receipt reason")
        if self.status in {"adapter_accepted", "confirmed"} and self.reason_code is not None:
            raise ValueError("Successful admission or confirmation cannot contain a failure reason")
        if self.status == "adapter_accepted" and message_id is not None:
            raise ValueError("Adapter admission is not provider confirmation")


def accept_notification_receipt(
    current: NotificationDeliveryReceipt | None,
    incoming: NotificationDeliveryReceipt,
    expected_fence: NotificationDeliveryFence,
    *,
    expected_revision: int | None,
    reconciled: bool = False,
) -> NotificationDeliveryReceipt:
    """Apply one optimistic receipt transition without implying another send.

    The transaction owner must authenticate the original app/connection, recheck
    current authorization and persist this result and immutable history atomically.
    ``reconciled`` records explicit provider reconciliation, never an automatic
    retry. Unknown outcomes cannot return to admission. Replays with identical
    evidence return the retained receipt without increasing its revision.
    """
    if type(reconciled) is not bool:
        raise ValueError("Reconciliation flag must be boolean")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("Invalid expected receipt revision")
    if not isinstance(incoming, NotificationDeliveryReceipt) or not isinstance(expected_fence, NotificationDeliveryFence):
        raise ValueError("Notification receipt and event fence are required")
    if incoming.revision != 1:
        raise ValueError("Incoming receipt revision is host assigned")
    if incoming.fence != expected_fence or (current is not None and current.fence != expected_fence):
        raise NotificationReceiptConflict("Notification event fence changed")
    if current is None:
        if expected_revision is not None:
            raise NotificationReceiptConflict("Notification receipt does not exist")
        return incoming
    if expected_revision != current.revision:
        raise NotificationReceiptConflict("Notification receipt revision changed")
    if (incoming.status, incoming.provider_message_id, incoming.reason_code) == (
        current.status, current.provider_message_id, current.reason_code
    ):
        return current
    if current.status in {"confirmed", "rejected"}:
        raise NotificationReceiptConflict("Notification receipt is terminal")
    if current.status == "unknown" and (not reconciled or incoming.status not in {"confirmed", "rejected"}):
        raise NotificationReceiptConflict("Unknown delivery requires provider reconciliation")
    if current.provider_message_id is not None and incoming.provider_message_id != current.provider_message_id:
        raise NotificationReceiptConflict("Provider message identity changed")
    return replace(incoming, revision=current.revision + 1)
