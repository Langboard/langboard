"""Short-lived authentication between Langboard backend services and Graph."""

from jwt import decode, encode
from ...Env import Env
from ..types import SafeDateTime


GRAPH_SERVICE_AUDIENCE = "langboard-graph"


def graph_service_headers() -> dict[str, str]:
    now = int(SafeDateTime.now().timestamp())
    token = encode(
        {
            "sub": "langboard-service",
            "iss": Env.PROJECT_NAME,
            "aud": GRAPH_SERVICE_AUDIENCE,
            "internal": "graph_service",
            "iat": now,
            "exp": now + 300,
        },
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def verify_graph_service_token(token: str) -> bool:
    try:
        payload = decode(
            token,
            Env.JWT_SECRET_KEY,
            algorithms=[Env.JWT_ALGORITHM],
            audience=GRAPH_SERVICE_AUDIENCE,
            issuer=Env.PROJECT_NAME,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
        return (
            payload.get("sub") == "langboard-service"
            and payload.get("internal") == "graph_service"
            and 0 < payload["exp"] - payload["iat"] <= 300
        )
    except Exception:
        return False
