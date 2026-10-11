"""Explicit app event delivery; no scheduler or implicit runtime start."""

from ...domain.services.AppEventDelivery import claim_app_event, finish_app_event, revalidate_app_event_claim
from .utils import ensure_public_webhook_url
from .WebhookTask import EXECUTION_WEBHOOK_TIMEOUT, post_resolved_webhook_bytes


class AppEventDeliveryFailed(RuntimeError):
    """A bounded HTTP attempt failed; retry state remains durable."""


async def deliver_app_event(event_id, expected_destination_revision):
    claim = claim_app_event(event_id, expected_destination_revision)
    if claim is None:
        return None
    try:
        target = await ensure_public_webhook_url(claim["url"])
        current = revalidate_app_event_claim(event_id, claim["claim_token"], expected_destination_revision)
        if current is None:
            return None
        if current["url"] != claim["url"] or current["body"] != claim["body"]:
            raise AppEventDeliveryFailed("Delivery snapshot changed")
        await post_resolved_webhook_bytes(
            target, current["body"], current["headers"], timeout=EXECUTION_WEBHOOK_TIMEOUT
        )
    except Exception as error:
        finish_app_event(event_id, claim["claim_token"], delivered=False)
        raise AppEventDeliveryFailed("App event HTTP delivery failed") from error
    return finish_app_event(event_id, claim["claim_token"], delivered=True)


async def recover_app_events(limit=25):
    """Recover a bounded page of committed events; never reset failed/blocked state."""
    from sqlalchemy import or_
    from ...core.db import DbSession, SqlBuilder
    from ...core.types import SafeDateTime
    from ...domain.models import AppExecutionOutbox
    from ...domain.services.AppEventDestination import APP_EVENTS

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Recovery limit must be between 1 and 100")
    with DbSession.use(readonly=False) as db:
        events = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.event_type.in_(APP_EVENTS),
                AppExecutionOutbox.payload["destination"]["revision"].as_integer().is_not(None),
                or_(
                    AppExecutionOutbox.state == "pending",
                    (AppExecutionOutbox.state == "delivering") & (AppExecutionOutbox.lease_until <= SafeDateTime.now()),
                ),
            )
            .order_by(AppExecutionOutbox.id)
            .limit(limit)
        ).all()
    processed = 0
    for event in events:
        # Target revision was frozen when the response was accepted or first signed.
        snapshot = event.payload.get("destination")
        if not isinstance(snapshot, dict) or type(snapshot.get("revision")) is not int:
            continue
        try:
            result = await deliver_app_event(event.id, snapshot["revision"])
        except AppEventDeliveryFailed:
            # The delivery worker retained its failure/attempt receipt.
            continue
        if result is not None:
            processed += 1
    return processed


if __name__ == "__main__":
    from asyncio import run

    print(f"processed={run(recover_app_events())}")
