from secrets import compare_digest
from langboard_shared.core.routing import BaseMiddleware, JsonResponse
from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token
from langboard_shared.Env import Env
from starlette.datastructures import Headers
from starlette.types import Receive, Scope, Send


class GraphServiceAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1/graph/"):
            authorization = Headers(scope=scope).get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            socket_secret = Env.SOCKET_PHOENIX_INTERNAL_SECRET
            supplied_secret = Headers(scope=scope).get("x-socket-internal-secret", "")
            socket_authenticated = len(socket_secret) >= 32 and compare_digest(
                supplied_secret.encode(), socket_secret.encode()
            )
            jwt_authenticated = scheme.lower() == "bearer" and verify_graph_service_token(token)
            if not socket_authenticated and not jwt_authenticated:
                response = JsonResponse(content={"message": "Graph service authentication required"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
