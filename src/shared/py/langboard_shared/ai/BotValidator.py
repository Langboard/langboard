from ..core.routing import BaseFormModel
from ..domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType


class BaseSharedBotForm(BaseFormModel):
    platform: BotPlatform
    platform_running_type: BotPlatformRunningType
    api_url: str = ""
    api_key: str = ""
    value: str = ""


def validate_bot_form(form: BaseSharedBotForm) -> bool:
    is_valid = False
    if form.platform == BotPlatform.Default:
        if form.platform_running_type == BotPlatformRunningType.Default:
            is_valid = bool(form.value)
    elif form.platform == BotPlatform.Langflow:
        if form.platform_running_type == BotPlatformRunningType.Endpoint:
            is_valid = bool(form.api_url) and bool(form.api_key) and bool(form.value)
    elif form.platform == BotPlatform.N8N:
        if form.platform_running_type == BotPlatformRunningType.Default:
            is_valid = bool(form.api_url) and bool(form.api_key) and bool(form.value)

    return is_valid
