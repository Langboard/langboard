"""Encrypted, bounded HTTP transport for the configured desktop push provider."""

import json
from datetime import timedelta
from time import time
from jwt import encode
from pywebpush import WebPusher
from requests import Session
from ...Env import Env
from .WebPushSubscription import WebPushRegistration, browser_push_public_key


class NoRedirectSession(Session):
    def post(self, url, data=None, json=None, **kwargs):
        return super().post(url, data=data, json=json, **{**kwargs, "allow_redirects": False})


def send_reminder_push(subscription: WebPushRegistration, path: str) -> tuple[int, str | None]:
    public_key = browser_push_public_key()
    private_key = Env.get_from_env("CARD_REMINDER_WEB_PUSH_PRIVATE_KEY", "")
    contact = Env.get_from_env("CARD_REMINDER_WEB_PUSH_CONTACT", "")
    if not public_key or not private_key or not contact.startswith("mailto:"):
        return 0, None
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
        headers = {"Authorization": f"vapid t={token}, k={public_key}"}
        response = push.send(data=json.dumps({"version": 1, "path": path}), headers=headers, ttl=600, timeout=5)
        return response.status_code, response.headers.get("Retry-After")
