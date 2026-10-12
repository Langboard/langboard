"""Signed owning-app delivery using the existing broker and URL policy."""

from httpx import AsyncClient, HTTPStatusError
from ...core.broker import Broker
from ...core.types import SnowflakeID
from ...domain.services.AppEventDelivery import AppEventBusy, claim_event, finish_event, prepare_claim
from ...domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from .utils import ensure_public_webhook_url
from .WebhookTask import WEBHOOK_TIMEOUT


class AppEventRetry(RuntimeError):
    pass


@Broker.wrap_async_task_decorator(
    {
        "name": "langboard.app_event_delivery",
        "autoretry_for": (AppEventRetry, AppEventBusy),
        "retry_backoff": 60,
        "retry_backoff_max": 600,
        "retry_jitter": True,
        "retry_kwargs": {"max_retries": 8},
        "acks_late": True,
        "reject_on_worker_lost": True,
    }
)
async def app_event_delivery_task(event_id: int):
    await deliver_app_event(SnowflakeID(event_id))


async def deliver_app_event(event_id):
    token = claim_event(event_id)
    if token is None:
        return
    try:
        prepared = prepare_claim(event_id, token)
        target = await ensure_public_webhook_url(prepared["url"])
        # Revalidate after DNS before the bounded send. Revocation after this
        # admission cannot recall bytes already sent; ACK still rechecks authority.
        current = prepare_claim(event_id, token)
        if current["url"] != prepared["url"] or current["body"] != prepared["body"]:
            raise AppGovernanceConflict()
        headers = {**current["headers"], "Host": target.host_header}
        async with AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as client:
            response = await client.post(
                target.url, content=current["body"], headers=headers, extensions={"sni_hostname": target.sni_hostname}
            )
            response.raise_for_status()
    except (AppGovernanceDenied, AppGovernanceConflict, ValueError) as exc:
        finish_event(event_id, token, success=False, error=type(exc).__name__)
        return
    except HTTPStatusError as exc:
        retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
        retry = finish_event(
            event_id, token, success=False, error=f"http_{exc.response.status_code}", retryable=retryable
        )
        if retry:
            raise AppEventRetry("App event delivery requires retry") from exc
        return
    except Exception as exc:
        retry = finish_event(event_id, token, success=False, error=type(exc).__name__, retryable=True)
        if retry:
            raise AppEventRetry("App event delivery requires retry") from exc
        return
    finish_event(event_id, token, success=True)


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
