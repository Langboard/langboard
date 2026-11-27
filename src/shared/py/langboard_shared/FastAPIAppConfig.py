from os import getpid
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel
from uvicorn.config import LifespanType


class FastAPIAppConfigModel(BaseModel):
    host: str
    port: int
    uds: Optional[str] = None
    lifespan: LifespanType
    ssl_options: dict[str, Any] = {}
    workers: int
    timeout_keep_alive: int = 30
    healthcheck_interval: int = 30
    watch: bool = False
    is_restarting: bool = False


class FastAPIAppConfig:
    def __init__(self, config_file: str | Path):
        self.__config_file = Path(config_file)

    def create(
        self,
        host: str = "localhost",
        port: int = 5381,
        uds: Optional[str] = None,
        lifespan: LifespanType = "auto",
        ssl_options: Any = None,
        workers: int = 1,
        timeout_keep_alive: int = 30,
        healthcheck_interval: int = 30,
        watch: bool = False,
    ):
        self.__config_file.parent.mkdir(parents=True, exist_ok=True)
        self.__config_file.touch(exist_ok=True)

        config = FastAPIAppConfigModel.model_validate(
            {
                "host": host,
                "port": port,
                "uds": uds,
                "lifespan": lifespan,
                "ssl_options": ssl_options or {},
                "workers": workers,
                "timeout_keep_alive": timeout_keep_alive,
                "healthcheck_interval": healthcheck_interval,
                "watch": watch,
                "is_restarting": False,
            }
        )

        self.__write(config.model_dump_json())

    def load(self) -> FastAPIAppConfigModel:
        if not self.__config_file.exists():
            raise FileNotFoundError(f"Configuration file {self.__config_file} does not exist.")

        with open(self.__config_file, "r") as f:
            config = f.read()
        return FastAPIAppConfigModel.model_validate_json(config)

    def set_restarting(self, is_restarting: bool):
        config = self.load()
        config.is_restarting = is_restarting

        self.__write(config.model_dump_json())

    def __write(self, content: str) -> None:
        self.__config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.__config_file.with_name(f".{self.__config_file.name}.{getpid()}.tmp")
        with tmp_file.open("w") as f:
            f.write(content)
        tmp_file.replace(self.__config_file)
