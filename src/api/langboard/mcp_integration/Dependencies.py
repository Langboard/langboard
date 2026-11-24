"""Adapt existing domain annotations to FastMCP's native dependency lifecycle."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from inspect import Parameter, iscoroutinefunction, signature
from typing import get_args
from anyio import CancelScope
from fastmcp.dependencies import Depends
from fastmcp.exceptions import AuthorizationError
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
from langboard_shared.domain.models import Bot, User
from langboard_shared.domain.services import DomainService
from langboard_shared.infrastructure.repositories import Repository
from ..middlewares.McpAuthMiddleware import mcp_auth_context


def _actor_dependency(annotation):
    allowed = {item for item in get_args(annotation) if item is not type(None)} or {annotation}

    def actor():
        auth = mcp_auth_context.get()
        value = auth.get("user_or_bot") if auth else None
        if not isinstance(value, tuple(allowed)):
            reason = "User" if allowed == {User} else "Bot" if allowed == {Bot} else "Authentication"
            raise AuthorizationError(
                f"{reason} authentication required" if reason != "Authentication" else "Authentication required"
            )
        return value

    return actor


def _factory_dependency(factory_type):
    @asynccontextmanager
    async def resource():
        # Construction and close can perform blocking database work. Shield both
        # boundaries so cancellation cannot strand a newly allocated factory.
        with CancelScope(shield=True):
            value = await call_sync_fn_in_threadpool(factory_type)
        try:
            yield value
        finally:
            with CancelScope(shield=True):
                await call_sync_fn_in_threadpool(value.close)

    return resource


def with_native_dependencies(handler: Callable):
    """Map annotations once; FastMCP resolves and owns each call's resources."""
    sig = signature(handler)
    parameters = []
    for parameter in sig.parameters.values():
        annotation = parameter.annotation
        members = {item for item in get_args(annotation) if item is not type(None)} or {annotation}
        if members <= {User, Bot}:
            dependency = _actor_dependency(annotation)
        elif annotation in (DomainService, Repository):
            # A distinct provider per parameter preserves separate factory ownership.
            dependency = _factory_dependency(annotation)
        else:
            parameters.append(parameter.replace(kind=Parameter.KEYWORD_ONLY))
            continue
        parameters.append(parameter.replace(kind=Parameter.KEYWORD_ONLY, default=Depends(dependency)))

    @wraps(handler)
    async def invoke(**kwargs):
        if iscoroutinefunction(handler):
            return await handler(**kwargs)
        return await call_sync_fn_in_threadpool(handler, **kwargs)

    invoke.__signature__ = sig.replace(parameters=parameters)
    return invoke
