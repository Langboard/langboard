"""Encrypted, bounded HTTP transport for the configured desktop push provider."""

import json
from datetime import timedelta
from time import time
from jwt import encode
from pywebpush import WebPusher
from requests import Session
from .WebPushSubscription import WebPushRegistration, browser_push_vapid_config


class NoRedirectSession(Session):
    def post(self, url, data=None, json=None, **kwargs):
        return super().post(url, data=data, json=json, **{**kwargs, "allow_redirects": False})


def send_reminder_push(
    subscription: WebPushRegistration,
    path: str,
    *,
    urgent: bool = False,
    preview: str | None = None,
    event_uid: str | None = None,
) -> tuple[int, str | None]:
    config = browser_push_vapid_config()
    if config is None:
        return 0, None
    public_key, private_key, contact = config
    token = encode(
        {
            "aud": "https://fcm.googleapis.com",
            "exp": int(time() + timedelta(minutes=5).total_seconds()),
            "sub": contact,
        },
        private_key,
        algorithm="ES256",
    )
    endpoint = subscription.endpoint.get_secret_value()
    keys = {"p256dh": subscription.keys.p256dh.get_secret_value(), "auth": subscription.keys.auth.get_secret_value()}
    with NoRedirectSession() as session:
        push = WebPusher({"endpoint": endpoint, "keys": keys}, requests_session=session, verbose=False)
        headers = {"Authorization": f"vapid t={token}, k={public_key}", "Urgency": "high" if urgent else "normal"}
        payload: dict = {"version": 1, "path": path}
        if urgent and event_uid:
            payload.update(urgent=True, event_uid=event_uid)
        if preview:
            payload["preview"] = preview
        response = push.send(data=json.dumps(payload), headers=headers, ttl=600, timeout=5)
        return response.status_code, response.headers.get("Retry-After")
