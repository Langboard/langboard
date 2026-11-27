from json import dumps as json_dumps
from os import getpid
from pathlib import Path
from typing import Any, Callable, TypeVar, cast
from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from ...Env import Env
from ..utils.decorators import class_instance, thread_safe_singleton
from .ApiErrorCode import ApiErrorCode
from .ApiPermission import ApiPermission
from .ApiSchemaHelper import ApiSchemaHelper, ApiSchemaMap
from .AppExceptionHandlingRoute import AppExceptionHandlingRoute


TApiRouteMap = dict[str, ApiSchemaMap]
_TRoute = TypeVar("_TRoute", bound=Callable[..., Any])


@class_instance()
@thread_safe_singleton
class AppRouter:
    """Manages the application's all routers.

    Attributes:
        `open_api_schema_file` (`str`): The OpenAPI schema file name.
        `api_routes_file` (`str`): The API routes file name.
        `api_routes` (`dict[str, ApiSchemaMap]`): The API route schema map.
        `api` (`APIRouter`): The API router.
    """

    open_api_schema_file: str = "openapi.json"
    api_routes_file: str = "api_routes.json"
    api_routes: TApiRouteMap = {}
    api: APIRouter
    __app: FastAPI

    def __init__(self):
        self.api = APIRouter(route_class=AppExceptionHandlingRoute)

    def schema(
        self,
        *,
        query: type[BaseModel] | None = None,
        form: type[BaseModel] | None = None,
        file_field: str | None = None,
        permission: ApiPermission,
    ):
        def wrapper(func: _TRoute) -> _TRoute:
            setattr(func, "_schema", {"query": query, "form": form, "file_field": file_field, "permission": permission})
            return func

        return wrapper

    def set_openapi_schema(self, app: FastAPI):
        if app.openapi_schema:
            openapi_schema = app.openapi_schema
        else:
            openapi_schema = get_openapi(
                title=Env.PROJECT_NAME.capitalize(),
                version=Env.PROJECT_VERSION,
                routes=app.routes,
            )

        openapi_schema["info"]["title"] = Env.PROJECT_NAME.capitalize()
        openapi_schema["info"]["version"] = Env.PROJECT_VERSION

        if "components" not in openapi_schema:
            openapi_schema["components"] = {}

        if "schemas" not in openapi_schema["components"]:
            openapi_schema["components"]["schemas"] = {}

        for error_code in ApiErrorCode.__members__.values():
            openapi_schema["components"]["schemas"][f"ApiErrorCode{error_code.name}"] = {
                "title": error_code.name,
                "type": "object",
                "properties": {
                    "code": {
                        "title": "Error Code",
                        "type": "string",
                    },
                    "message": {
                        "title": "Error Message",
                        "type": "string",
                    },
                },
                "example": {
                    "code": error_code.name,
                    "message": error_code.value,
                },
            }

        openapi_schema["components"]["schemas"]["EmptyResponse"] = {
            "title": "EmptyResponse",
            "type": "object",
            "properties": {},
        }

        openapi_schema["components"]["schemas"]["ValidationError"] = {
            "title": "ValidationError",
            "type": "object",
            "properties": {
                "code": {
                    "title": "Error Code",
                    "type": "string",
                },
                "message": {
                    "title": "Error Message",
                    "type": "string",
                },
                "<error type>": {
                    "title": "Error Type",
                    "type": "object",
                    "properties": {
                        "Enum[body, query, path, header]": {
                            "title": "Location",
                            "type": "array",
                            "items": {"anyOf": [{"type": "string", "example": "<field name>"}]},
                        }
                    },
                },
            },
        }

        app.openapi_schema = openapi_schema

    def set_app(self, app: FastAPI):
        self.__app = app
        self.__app.title = Env.PROJECT_NAME.capitalize()
        self.__app.version = Env.PROJECT_VERSION

    def create_schema_files(self, schema_dir: str | Path):
        schema_dir = Path(schema_dir)
        if not self.__app:
            raise ValueError("AppRouter has not been initialized with a FastAPI instance.")

        open_api_file = schema_dir / self.open_api_schema_file
        api_routes_file = schema_dir / self.api_routes_file

        self.__write_json_file(open_api_file, self.__app.openapi_schema)
        self.__app.openapi_schema = None

        for route in self.api.routes:
            route = cast(AppExceptionHandlingRoute, route)
            name = route.name
            if not hasattr(route.endpoint, "_schema"):
                continue

            self.api_routes[name] = ApiSchemaHelper.create_schema(route)

        self.__write_json_file(api_routes_file, self.api_routes)

    def get_app(self) -> FastAPI:
        if not self.__app:
            raise ValueError("AppRouter has not been initialized with a FastAPI instance.")
        return self.__app

    @staticmethod
    def __write_json_file(path: Path, content: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(json_dumps(content))
        tmp_path.replace(path)
