"""Probe selected internal ingresses from the API consumer, without logging URLs.

Readiness is not authenticated socket/editor/callback acceptance. No credentials
are added and redirects are not followed; the configured route must be explicit.
"""

import os
import time
import httpx


def check_ingresses() -> None:
    for name in ("SOCKET_INTERNAL_URL", "SOCKET_EDITOR_INTERNAL_URL"):
        url = os.environ.get(name, "").rstrip("/")
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            raise RuntimeError(f"{name} is not a valid service URL") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.host
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(f"{name} must be an explicit HTTP(S) service URL")
        with httpx.Client(timeout=2, follow_redirects=False) as client:
            for attempt in range(20):
                try:
                    response = client.head(f"{url}/health/ready")
                    if (
                        response.status_code == 204
                        and response.headers.get("x-langboard-socket-runtime") == "phoenix"
                        and response.headers.get("x-langboard-socket-version", "").strip()
                    ):
                        print(f"{name}: compatible Phoenix readiness response received")
                        break
                except httpx.HTTPError:
                    pass
                if attempt < 19:
                    time.sleep(1)
            else:
                raise RuntimeError(f"{name}: no compatible Phoenix readiness response; API/UI remain running")


if __name__ == "__main__":
    check_ingresses()
