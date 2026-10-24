"""Authenticated personal avatar operations; external generation requires consent."""

from fastapi import File, Form, UploadFile
from httpx import TimeoutException
from langboard_shared.ai.image_generation import ImageGenerationError
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.AvatarService import AvatarError, AvatarService
from langboard_shared.security import Auth
from pydantic import BaseModel, Field


class AvatarPreviewBody(BaseModel):
    profile_uid: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


class AvatarRestoreBody(BaseModel):
    expected_revision: int = Field(ge=0, strict=True)


def avatar_response(action):
    try:
        return JsonResponse(content=action(), headers={"Cache-Control": "no-store"})
    except (AvatarError, ImageGenerationError) as error:
        return JsonResponse(content={"error": error.code}, status_code=error.status)
    except ImportError:
        return JsonResponse(content={"error": "avatar_processing_unavailable"}, status_code=503)
    except ValueError:
        return JsonResponse(content={"error": "avatar_invalid_image"}, status_code=400)
    except Exception:
        return JsonResponse(content={"error": "avatar_unavailable"}, status_code=503)


async def avatar_preview_response(service, user, body, project_uid=None, *, bot_draft=False):
    try:
        result = await AvatarService(service).preview(
            user, body.profile_uid, body.prompt, body.confirmed, project_uid, bot_draft=bot_draft
        )
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except (AvatarError, ImageGenerationError) as error:
        return JsonResponse(content={"error": error.code}, status_code=error.status)
    except (TimeoutException, TimeoutError):
        return JsonResponse(content={"error": "avatar_generation_timeout"}, status_code=504)
    except ImportError:
        return JsonResponse(content={"error": "avatar_processing_unavailable"}, status_code=503)
    except Exception:
        return JsonResponse(content={"error": "avatar_generation_unavailable"}, status_code=502)


@AppRouter.api.get("/account/avatar", tags=["Account"])
@AuthFilter.add("user")
def get_avatar(user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    return avatar_response(lambda: AvatarService(service).get(user))


@AppRouter.api.put("/account/avatar", tags=["Account"])
@AuthFilter.add("user")
def apply_avatar(
    expected_revision: int = Form(..., ge=0),
    avatar: UploadFile = File(...),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return avatar_response(lambda: AvatarService(service).mutate(user, expected_revision, upload=avatar))


@AppRouter.api.post("/account/avatar/restore", tags=["Account"])
@AuthFilter.add("user")
def restore_avatar(
    body: AvatarRestoreBody, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return avatar_response(lambda: AvatarService(service).mutate(user, body.expected_revision, restore=True))


@AppRouter.api.get("/account/avatar/image-profiles", tags=["Account"])
@AuthFilter.add("user")
def avatar_profiles(user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    return avatar_response(lambda: AvatarService(service).profiles(user))


@AppRouter.api.post("/account/avatar/preview", tags=["Account"])
@AuthFilter.add("user")
async def preview_avatar(
    body: AvatarPreviewBody, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return await avatar_preview_response(service, user, body)
