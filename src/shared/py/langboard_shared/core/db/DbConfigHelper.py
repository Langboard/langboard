from typing import Any, Literal
from sqlalchemy import NullPool, StaticPool
from ...Env import Env
from ..utils.decorators import staticclass


@staticclass
class DbConfigHelper:
    @staticmethod
    def create_config(url: str) -> dict[str, Any]:
        driver_type = DbConfigHelper.get_driver_type(url)
        if driver_type == "sqlite":
            is_memory_database = ":memory:" in url
            return {
                "connect_args": {
                    "check_same_thread": False,
                    "timeout": Env.DB_TIMEOUT,
                },
                "poolclass": StaticPool if is_memory_database else NullPool,
                "pool_pre_ping": True,
            }

        if driver_type == "postgresql":
            return {
                "connect_args": {
                    "application_name": f"{Env.PROJECT_NAME}_{Env.WORKER}",
                    "connect_timeout": Env.DB_TIMEOUT,
                    "prepare_threshold": None,
                    "tcp_user_timeout": Env.DB_TCP_USER_TIMEOUT,
                },
                "pool_pre_ping": True,
                "pool_size": Env.DB_POOL_SIZE,
                "max_overflow": Env.DB_MAX_OVERFLOW,
                "pool_timeout": Env.DB_POOL_TIMEOUT,
                "pool_recycle": Env.DB_POOL_RECYCLE,
                "query_cache_size": Env.DB_QUERY_CACHE_SIZE,
            }

        return {}

    @staticmethod
    def get_sanitized_driver(url: str) -> str:
        split_url = url.split("://", maxsplit=1)
        driver_type = DbConfigHelper.get_driver_type(url)
        if driver_type == "sqlite":
            return f"sqlite://{split_url[1]}"
        if driver_type == "postgresql":
            return f"postgresql+psycopg://{split_url[1]}"
        return url

    @staticmethod
    def get_driver_type(url: str) -> Literal["sqlite", "postgresql"] | str:
        split_url = url.split("://", maxsplit=1)
        driver = split_url[0].split("+", maxsplit=1)[0]
        if driver == "sqlite":
            return "sqlite"
        if driver in ("postgresql", "postgres"):
            return "postgresql"
        return driver
