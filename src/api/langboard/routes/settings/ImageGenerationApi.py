"""Administrator-only image binding diagnostics. Reads never invoke generation."""

from asyncio import timeout
from anyio import to_thread
from httpx import TimeoutException
from langboard_shared.ai.image_generation import (
    SUPPORTED_PROVIDERS,
    ImageGenerationError,
    binding_signature,
    bounded_response,
    generate_preview,
    provider_target,
    validate_image_config,
)
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.domain.models import InternalBot, SettingRole
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.helpers import InfraHelper
from langboard_shared.security import RoleFinder
from pydantic import Field


def image_binding_snapshot(service, uid):
    binding = image_binding(service, uid)
    return binding, binding_signature(binding)


def image_binding(service, uid):
    binding = service.internal_bot.get_current_by_id_like(uid)
    if not binding or binding.bot_type != InternalBotType.ImageGeneration:
        raise ImageGenerationError("image_generation_not_found", 404)
    if binding.platform != BotPlatform.Default or binding.platform_running_type != BotPlatformRunningType.Default:
        raise ImageGenerationError("image_generation_unavailable", 409)
    return binding


@AppRouter.api.get("/settings/image-generation/capabilities", tags=["AppSettings.InternalBot"])
@RoleFilter.add(SettingRole, [SettingRoleAction.InternalBotUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_image_capabilities() -> JsonResponse:
    profiles = []
    for binding in InfraHelper.get_all(InternalBot):
        if binding.bot_type != InternalBotType.ImageGeneration:
            continue
        try:
            config = validate_image_config(binding.value)
        except (ValueError, TypeError):
            continue
        profiles.append(
            {
                "uid": binding.get_uid(),
                "display_name": binding.display_name,
                "enabled": config.enabled,
                "is_default": binding.is_default,
                "model_name": config.model_name,
            }
        )
    return JsonResponse(
        content={
            "providers": list(SUPPORTED_PROVIDERS),
            "profiles": profiles,
            "configured": any(profile["enabled"] for profile in profiles),
        }
    )


@form_model
class ImagePreviewForm(BaseFormModel):
    prompt: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


@AppRouter.api.post("/settings/image-generation/{internal_bot_uid}/preview", tags=["AppSettings.InternalBot"])
@RoleFilter.add(SettingRole, [SettingRoleAction.InternalBotUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
async def preview_image(
    internal_bot_uid: str,
    form: ImagePreviewForm = ImagePreviewForm.scope(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    if form.confirmed is not True or not form.prompt.strip():
        return JsonResponse(content={"error": "image_generation_confirmation_required"}, status_code=400)
    try:
        binding, signature = await to_thread.run_sync(image_binding_snapshot, service, internal_bot_uid)
        async with timeout(60):
            preview = await generate_preview(binding, form.prompt.strip())
        _, current_signature = await to_thread.run_sync(image_binding_snapshot, service, internal_bot_uid)
        if signature != current_signature:
            raise ImageGenerationError("image_generation_binding_changed", 409)
        return JsonResponse(content={"preview_data_url": preview, "width": 512, "height": 512})
    except ImageGenerationError as error:
        return JsonResponse(content={"error": error.code}, status_code=error.status)
    except (TimeoutException, TimeoutError):
        return JsonResponse(content={"error": "image_generation_timeout"}, status_code=504)
    except ImportError:
        return JsonResponse(content={"error": "image_generation_unavailable"}, status_code=503)
    except Exception:
        return JsonResponse(content={"error": "image_generation_provider_unavailable"}, status_code=502)


@AppRouter.api.get("/settings/image-generation/{internal_bot_uid}/models", tags=["AppSettings.InternalBot"])
@RoleFilter.add(SettingRole, [SettingRoleAction.InternalBotUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
async def get_image_models(internal_bot_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    from json import loads
    from httpx import AsyncClient
    from langboard_shared.core.security import KeyVault
    from langboard_shared.tasks.webhooks.utils import ResolvedWebhookTarget

    try:
        binding, signature = await to_thread.run_sync(image_binding_snapshot, service, internal_bot_uid)
        config = validate_image_config(binding.value)
        target = await provider_target(config.base_url)
        target = ResolvedWebhookTarget(
            target.url.removesuffix("images/generations") + "models", target.host_header, target.sni_hostname
        )
        headers = {"Accept": "application/json"}
        if binding.api_key:
            headers["Authorization"] = "Bearer " + await to_thread.run_sync(KeyVault.get_key, binding.api_key)
        async with AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = loads(await bounded_response(client, "GET", target, headers=headers, limit=1024 * 1024))
        records = response.get("data")
        if not isinstance(records, list):
            raise ValueError("Invalid model list")
        models = sorted(
            {
                row["id"]
                for row in records
                if isinstance(row, dict) and isinstance(row.get("id"), str) and 0 < len(row["id"]) <= 256
            }
        )[:1000]
        _, current_signature = await to_thread.run_sync(image_binding_snapshot, service, internal_bot_uid)
        if signature != current_signature:
            raise ImageGenerationError("image_generation_binding_changed", 409)
        return JsonResponse(content={"models": models})
    except ImageGenerationError as error:
        return JsonResponse(content={"models": [], "error": error.code}, status_code=error.status)
    except Exception:
        return JsonResponse(content={"models": [], "error": "image_generation_provider_unavailable"}, status_code=502)
