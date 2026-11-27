from fastapi.responses import JSONResponse
from langboard_shared.core.caching import Cache
from langboard_shared.core.routing import AppRouter
from ..core.graph.GraphRunner import GraphRunner


@AppRouter.api.get("/health")
def health_check():
    return JSONResponse(content={"status": "ok", "runtime": "graph"})


@AppRouter.api.get("/bot/status/map")
def bot_status_map(project_uid: str):
    status_map = Cache.get(GraphRunner._get_bot_status_cache_key(project_uid))
    if status_map is None:
        legacy_status_map = Cache.get(GraphRunner.BOT_STATUS_MAP_CACHE_PREFIX) or {}
        status_map = legacy_status_map.get(project_uid, {})
    return JSONResponse(content={"status_map": status_map})
