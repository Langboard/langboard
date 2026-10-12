"""Queue the canonical signed delivery worker through the shared broker."""

from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ...domain.models import AppExecutionOutbox
from .AppEventDeliveryWorker import AppEventDeliveryFailed, deliver_app_event


@Broker.wrap_async_task_decorator(
    {
        "name": "langboard.app_event_delivery",
        "autoretry_for": (AppEventDeliveryFailed,),
        "retry_backoff": 60,
        "retry_backoff_max": 600,
        "retry_jitter": True,
        "retry_kwargs": {"max_retries": 8},
        "acks_late": True,
        "reject_on_worker_lost": True,
    }
)
async def app_event_delivery_task(event_id: int):
    # Use the destination accepted with this event, never the latest revision.
    with DbSession.use(readonly=False) as db:
        event = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.id == event_id)
        ).first()
        destination = event.payload.get("destination") if event else None
    if not isinstance(destination, dict) or type(destination.get("revision")) is not int:
        return
    await deliver_app_event(SnowflakeID(event_id), destination["revision"])


@Broker.wrap_async_task_decorator({"name": "langboard.app_event_recover"})
async def app_event_recover_task():
    from ...domain.services.AppEventDelivery import recover_pending_events

    cursor = None
    while True:
        cursor = recover_pending_events(cursor)
        if cursor is None:
            return


@Broker.on_worker_process_init
def recover_app_events_on_worker_start(**kwargs):
    """Recover committed outbox rows after queue publication/process failures."""
    Broker.celery.send_task("langboard.app_event_recover")
