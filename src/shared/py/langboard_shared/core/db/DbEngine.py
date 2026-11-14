from typing import Optional
from sqlalchemy import Engine, create_engine
from ...Env import Env
from ..utils.decorators import class_instance, thread_safe_singleton
from .DbConfigHelper import DbConfigHelper
from .ReadQueryTiming import instrument_read_queries


@class_instance()
@thread_safe_singleton
class DbEngine:
    __main_engine: Optional[Engine] = None
    __readonly_engine: Optional[Engine] = None

    def get_main_engine(self) -> Engine:
        if self.__main_engine:
            return self.__main_engine

        url = DbConfigHelper.get_sanitized_driver(Env.MAIN_DATABASE_URL)
        self.__main_engine = instrument_read_queries(
            create_engine(
                url,
                **DbConfigHelper.create_config(url),
            )
        )
        return self.__main_engine

    def get_readonly_engine(self) -> Engine:
        from ...domain.services.NativeMcpBoundary import native_mcp_boundary

        if native_mcp_boundary.get() is not None:
            return self.get_main_engine()
        if self.__readonly_engine:
            return self.__readonly_engine

        url = DbConfigHelper.get_sanitized_driver(Env.READONLY_DATABASE_URL)
        self.__readonly_engine = instrument_read_queries(
            create_engine(
                url,
                **DbConfigHelper.create_config(url),
            )
        )
        return self.__readonly_engine
