"""HTTP projection for app governance operations; authorization remains in services."""

from functools import partial
from langboard_shared.core.routing import ApiErrorCode, ApiException, JsonResponse
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied


def run_app_operation(operation, *args, error_codes=(None, None, None), **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(error_codes[0]) from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409(error_codes[1]) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(error_codes[2]) from exc


run_consent_operation = partial(
    run_app_operation, error_codes=(ApiErrorCode.PE1001, ApiErrorCode.EX3004, ApiErrorCode.VA0000)
)
