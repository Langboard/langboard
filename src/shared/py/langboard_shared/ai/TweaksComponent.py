from abc import ABC, abstractmethod
from typing import Any, Literal
from pydantic import BaseModel
from ..core.utils.EditorContentParser import DATA_TEXT_FORMAT_DESCRIPTIONS
from ..Env import Env


class TweaksComponent(ABC, BaseModel):
    @abstractmethod
    def to_tweaks(self) -> dict[str, Any]: ...

    @abstractmethod
    def to_data(self) -> dict[str, Any]: ...


class LangboardCalledVariablesComponent(TweaksComponent):
    event: str
    app_api_token: str
    project_uid: str | None = None
    current_runner_type: Literal["bot", "user"]
    current_runner_data: dict[str, Any] | None = None
    rest_data: dict[str, Any] | None = None
    custom_markdown_formats: dict[str, str] = DATA_TEXT_FORMAT_DESCRIPTIONS

    def to_tweaks(self) -> dict[str, Any]:
        return {LangboardCalledVariablesComponent.__name__: self.to_data()}

    def to_data(self) -> dict[str, Any]:
        return {"base_url": Env.API_URL, **self.model_dump()}


class LangboardCalledAPIToolsComponent(TweaksComponent):
    api_names: list[str]

    def to_tweaks(self) -> dict[str, Any]:
        return {LangboardCalledAPIToolsComponent.__name__: self.to_data()}

    def to_data(self) -> dict[str, Any]:
        return {"base_url": Env.API_URL, **self.model_dump()}
