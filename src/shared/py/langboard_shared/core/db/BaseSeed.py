from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, TypeVar
from ..logger import Logger
from .DbSession import DbSession
from .Models import BaseDbModel


class BaseSeed(ABC):
    logger = Logger.use("seed")
    TBaseDbModel = TypeVar("TBaseDbModel", bound=BaseDbModel)

    @staticmethod
    @abstractmethod
    def name() -> str: ...

    @abstractmethod
    def create_seed(self) -> AsyncGenerator[list[BaseDbModel], Any]: ...

    async def execute(self):
        if not self.name():
            raise ValueError("Seed name must be defined.")

        async for seed_batch in self.create_seed():
            with DbSession.use(readonly=False) as db:
                db.insert_all(seed_batch)
