"""Deliver one committed app event; recovery reuses its bounded durable state."""

from ...core.broker import Broker
from ...core.types import SnowflakeID
from .AppEventDeliveryWorker import AppEventDeliveryFailed, deliver_app_event


APP_EVENT_RETRY_OPTIONS = {
    "autoretry_for": (AppEventDeliveryFailed,),
    "retry_backoff": True,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "retry_kwargs": {"max_retries": 3},
}


@Broker.wrap_async_task_decorator(APP_EVENT_RETRY_OPTIONS)
async def app_event_delivery_task(event_uid: str, destination_revision: int) -> None:
    await deliver_app_event(SnowflakeID.from_short_code(event_uid), destination_revision)
