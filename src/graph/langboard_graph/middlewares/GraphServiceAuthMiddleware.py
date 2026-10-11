from langboard_shared.core.routing import BaseMiddleware, JsonResponse
from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token
from starlette.datastructures import Headers
from starlette.types import Receive, Scope, Send


class GraphServiceAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1/graph/"):
            authorization = Headers(scope=scope).get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not verify_graph_service_token(token):
                response = JsonResponse(content={"message": "Graph service authentication required"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
