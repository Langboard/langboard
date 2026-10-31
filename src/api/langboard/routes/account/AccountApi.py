from fastapi import File, UploadFile, status
from langboard_shared.core.caching import Cache
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.routing.Exception import ValidationFailureException, ValidationFailureInfo
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.domain.models import User, UserGroup
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.factory.AvatarService import AvatarError
from langboard_shared.domain.services.ProjectCollections import project_collections
from langboard_shared.domain.services.UserAppPreferences import PreferenceConflict, app_preferences
from langboard_shared.Env import UI_QUERY_NAMES
from langboard_shared.security import Auth
from .AccountForm import (
    AddNewEmailForm,
    ChangePasswordForm,
    CreateUserGroupForm,
    EmailForm,
    UpdateAppPreferencesForm,
    UpdatePreferredLangForm,
    UpdateProfileForm,
    UpdateProjectCollectionsForm,
    UpdateUserGroupAssignedEmailForm,
    VerifyNewEmailForm,
)


def _app_preferences(user, **kwargs):
    try:
        return JsonResponse(content=app_preferences(user, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except PreferenceConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX1003) from exc


@AppRouter.api.get("/account/app-preferences", tags=["Account"])
@AuthFilter.add("user")
def get_app_preferences(user: User = Auth.scope("user")) -> JsonResponse:
    return _app_preferences(user)


@AppRouter.api.put("/account/app-preferences", tags=["Account"])
@AuthFilter.add("user")
def update_app_preferences(form: UpdateAppPreferencesForm, user: User = Auth.scope("user")) -> JsonResponse:
    return _app_preferences(
        user, pinned_apps=[pin.model_dump() for pin in form.pinned_apps], expected_revision=form.expected_revision
    )


@AppRouter.api.put("/account/profile", tags=["Account"], responses=OpenApiSchema().auth().get())
@AuthFilter.add("user")
def update_profile(
    form: UpdateProfileForm = UpdateProfileForm.scope(),
    avatar: UploadFile | None = File(None),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    file_model = Storage.upload(avatar, StorageName.Avatar) if avatar else None
    form_dict = form.model_dump()
    if file_model:
        form_dict["avatar"] = file_model

    try:
        service.user.update(user, form_dict)
    except AvatarError as error:
        if file_model:
            Storage.delete(file_model)
        return JsonResponse(content={"error": error.code}, status_code=error.status)
    except Exception:
        if file_model:
            Storage.delete(file_model)
        raise

    return JsonResponse()


@AppRouter.api.post(
    "/account/email",
    tags=["Account"],
    responses=(
        OpenApiSchema(201)
        .auth()
        .err(404, ApiErrorCode.NF1001)
        .err(304, ApiErrorCode.EX1001)
        .err(503, ApiErrorCode.OP1001)
        .get()
    ),
)
@AuthFilter.add("user")
def add_new_email(
    form: AddNewEmailForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    cache_key = service.user.create_cache_name("subemail", user.email)
    existed_user, subemail = service.user.get_by_email(form.new_email)
    if not form.is_resend:
        if existed_user:
            raise ValidationFailureException(
                ValidationFailureInfo(loc="body", field="new_email", inputs=form.model_dump())
            )

        service.user.create_subemail(user.id, form.new_email)
    else:
        if not existed_user or existed_user.id != user.id or not subemail:
            raise ApiException.NotFound_404(ApiErrorCode.NF1001)

        if subemail.verified_at:
            return JsonResponse(content=ApiErrorCode.EX1001, status_code=status.HTTP_304_NOT_MODIFIED)

        Cache.delete(cache_key)

    token_url = service.user.create_token_url(
        user, cache_key, UI_QUERY_NAMES.SUB_EMAIL_VERIFY_TOKEN, {"email": form.new_email}
    )
    result = service.email.send_template(
        user.preferred_lang, form.new_email, "subemail", {"recipient": user.firstname, "url": token_url}
    )
    if not result:
        raise ApiException.ServiceUnavailable_503(ApiErrorCode.OP1001)

    return JsonResponse(status_code=status.HTTP_201_CREATED)


@AppRouter.api.post(
    "/account/email/verify",
    tags=["Account"],
    responses=(
        OpenApiSchema()
        .auth()
        .err(404, ApiErrorCode.NF1001)
        .err(409, ApiErrorCode.NF1002)
        .err(304, ApiErrorCode.EX1001)
        .get()
    ),
)
@AuthFilter.add("user")
def verify_subemail(form: VerifyNewEmailForm, service: DomainService = DomainService.scope()) -> JsonResponse:
    user, cache_key, extra = service.user.validate_token_from_url("subemail", form.verify_token)
    if not user or not cache_key or not extra or "email" not in extra:
        raise ApiException.NotFound_404(ApiErrorCode.NF1001)

    existed_user, subemail = service.user.get_by_email(extra["email"])
    if not existed_user or user.id != existed_user.id:
        raise ApiException.NotFound_404(ApiErrorCode.NF1001)

    if not subemail:
        raise ApiException.Conflict_409(ApiErrorCode.NF1002)

    if subemail.verified_at:
        return JsonResponse(content=ApiErrorCode.EX1001, status_code=status.HTTP_304_NOT_MODIFIED)

    service.user.verify_subemail(subemail)

    Cache.delete(cache_key)

    return JsonResponse()


@AppRouter.api.put(
    "/account/email",
    tags=["Account"],
    responses=(
        OpenApiSchema()
        .auth()
        .err(404, ApiErrorCode.NF1001)
        .err(423, ApiErrorCode.AU1002)
        .err(304, ApiErrorCode.EX1002)
        .get()
    ),
)
@AuthFilter.add("user")
def change_primary_email(
    form: EmailForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    existed_user, subemail = service.user.get_by_email(form.email)
    if not existed_user or existed_user.id != user.id or not subemail:
        raise ApiException.NotFound_404(ApiErrorCode.NF1001)

    if not subemail.verified_at:
        raise ApiException.Locked_423(ApiErrorCode.AU1002)

    if existed_user.email == form.email:
        return JsonResponse(content=ApiErrorCode.EX1002, status_code=status.HTTP_304_NOT_MODIFIED)

    service.user.change_primary_email(user, subemail)
    return JsonResponse()


@AppRouter.api.delete(
    "/account/email",
    tags=["Account"],
    responses=OpenApiSchema().auth().err(404, ApiErrorCode.NF1001).err(406, ApiErrorCode.PE1002).get(),
)
@AuthFilter.add("user")
def delete_email(
    form: EmailForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    existed_user, subemail = service.user.get_by_email(form.email)
    if not existed_user or existed_user.id != user.id or not subemail:
        raise ApiException.NotFound_404(ApiErrorCode.NF1001)

    if existed_user.email == form.email:
        raise ApiException.NotAcceptable_406(ApiErrorCode.PE1002)

    service.user.delete_email(subemail)
    return JsonResponse()


@AppRouter.api.put("/account/password", tags=["Account"], responses=OpenApiSchema().auth().get())
@AuthFilter.add("user")
def change_password(
    form: ChangePasswordForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    if not user.check_password(form.current_password):
        raise ValidationFailureException(
            ValidationFailureInfo(loc="body", field="current_password", inputs=form.model_dump())
        )

    service.user.change_password(user, form.new_password)
    Auth.reset_user(user)

    return JsonResponse()


@AppRouter.api.post(
    "/account/group",
    tags=["Account"],
    responses=OpenApiSchema().suc({"user_group": (UserGroup, {"schema": {"users": [User]}})}, 201).auth().get(),
)
@AuthFilter.add("user")
def create_user_group(
    form: CreateUserGroupForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    group = service.user_group.create(user, form.name)
    api_group = group.api_response()
    api_group["users"] = service.user_group.get_api_user_email_list_by_group(group.id)
    return JsonResponse(content={"user_group": api_group}, status_code=status.HTTP_201_CREATED)


@AppRouter.api.put(
    "/account/group/{group_uid}/name",
    tags=["Account"],
    responses=OpenApiSchema().auth().err(404, ApiErrorCode.NF1003).get(),
)
@AuthFilter.add("user")
def change_user_group_name(
    group_uid: str,
    form: CreateUserGroupForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.user_group.change_name(user, group_uid, form.name)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF1003)

    return JsonResponse()


@AppRouter.api.put(
    "/account/group/{group_uid}/emails",
    tags=["Account"],
    responses=OpenApiSchema().suc({"users": [User]}).auth().err(404, ApiErrorCode.NF1003).get(),
)
@AuthFilter.add("user")
def update_user_group_assigned_emails(
    group_uid: str,
    form: UpdateUserGroupAssignedEmailForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.user_group.update_assigned_emails(user, group_uid, form.emails)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF1003)

    group_users = service.user_group.get_api_user_email_list_by_group(group_uid)

    return JsonResponse(content={"users": group_users})


@AppRouter.api.delete(
    "/account/group/{group_uid}",
    tags=["Account"],
    responses=OpenApiSchema().auth().err(404, ApiErrorCode.NF1003).get(),
)
@AuthFilter.add("user")
def delete_user_group(
    group_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.user_group.delete(user, group_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF1003)

    return JsonResponse()


@AppRouter.api.put(
    "/account/preferred-language",
    tags=["Account"],
    responses=OpenApiSchema().auth().err(404, ApiErrorCode.VA1003).get(),
)
@AuthFilter.add("user")
def update_preferred_language(
    form: UpdatePreferredLangForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.user.update_preferred_lang(user, form.lang)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.VA1003)

    return JsonResponse()


def _project_collections(user, **kwargs):
    try:
        return JsonResponse(content=project_collections(user, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except PreferenceConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX1003) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@AppRouter.api.get("/account/project-collections", tags=["Account"])
@AuthFilter.add("user")
def get_project_collections(user: User = Auth.scope("user")) -> JsonResponse:
    return _project_collections(user)


@AppRouter.api.put("/account/project-collections", tags=["Account"])
@AuthFilter.add("user")
def update_project_collections(form: UpdateProjectCollectionsForm, user: User = Auth.scope("user")) -> JsonResponse:
    return _project_collections(
        user, collections=[item.model_dump() for item in form.collections], expected_revision=form.expected_revision
    )
