from fastapi.datastructures import QueryParams
from langboard_shared.core.routing import BaseMiddleware


class QueryStringMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope["query_string"]:
            query_string = scope["query_string"].decode()
            if query_string.startswith("?"):
                query_string = query_string[1:]
            scope["query_string"] = query_string.encode()
            query_params = QueryParams(scope)
            scope["query_params"] = query_params

        await self.app(scope, receive, send)
