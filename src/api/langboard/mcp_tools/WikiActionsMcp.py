"""Native wiki read views share canonical queries and current authorization."""

from typing import Annotated, Any, Literal
from fastmcp.exceptions import AuthorizationError, ValidationError
from langboard_shared.core.exceptions.WikiContentConflict import WikiContentConflict
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.ProjectWikiService import WikiSecretSelection, WikiSecretSelectionError
from langboard_shared.security import RoleFinder
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from ..mcp_integration import McpRoleFilter, McpTool
from ..mcp_integration.ExecutionHistory import execution_scope, safe_scope
from ..mcp_integration.NativeConsent import native_owner_boundary
from ..mcp_integration.Outputs import CommandOutput, DeletedOutput, WikiRevisionOutput
from ..wiki_workspace.application import append_wiki, delete_wiki, patch_wiki, replace_wiki
from ..wiki_workspace.application import read_wiki as read_wiki_content
from ..wiki_workspace.domain import WikiValidationError
from ..wiki_workspace.infrastructure import NativeWikiRepository
from .RoleChecker import McpRoleChecker
from .WikiMcp import WikiTextEdit, _check_conversion_owner


@McpTool.add(
    "user",
    description="Read exact wiki content, revision history, or a stored revision. Follow returned cursors. History limit is at most 50; revision_uid/side require revision view.",
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def read_wiki(
    project_uid: str,
    wiki_uid: str,
    user: User,
    service: DomainService,
    view: Literal["content", "history", "revision"] = "content",
    revision_uid: str | None = None,
    side: Literal["before", "after"] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Field(ge=1, le=16000, strict=True)] | None = None,
) -> dict[str, Any]:
    if view != "revision" and (revision_uid is not None or side is not None):
        raise ValueError("revision_uid and side require revision view")
    if view == "revision" and (not revision_uid or not revision_uid.strip()):
        raise ValueError("Select a revision returned by history view")
    if view == "history" and limit is not None and limit > 50:
        raise ValueError("History limit must be at most 50")
    command = {
        "content": "read_wiki_content",
        "history": "list_wiki_revisions",
        "revision": "read_wiki_revision",
    }[view]
    arguments = {
        "project_uid": project_uid,
        "wiki_uid": wiki_uid,
        "cursor": cursor,
        "limit": limit if limit is not None else (20 if view == "history" else 8000),
    }
    if view == "revision":
        arguments.update(revision_uid=revision_uid, side=side or "after")
    with native_owner_boundary(command, arguments):
        _authorize_command(command, user, service, arguments, "Native wiki query is unavailable")
        repository = NativeWikiRepository(user, service)
        if view == "history":
            return repository.revisions(**arguments)
        if view == "revision":
            return repository.revision_page(**arguments)
        try:
            return read_wiki_content(repository, **arguments)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


def _authorize_command(command: str, user: User, service: DomainService, arguments: dict, unavailable: str) -> None:
    """Retain primitive transport authority without another invocation or service."""
    from ..mcp_integration.Server import McpServer

    metadata = McpTool.get_tool(command)
    if metadata is None:
        raise RuntimeError(unavailable)
    handler = metadata["handler"]
    if not McpServer._validate_auth(user, command):
        raise AuthorizationError("Authentication required")
    scope = execution_scope.get()
    if scope is not None:
        resolved = safe_scope(user, handler, arguments)
        if resolved:
            scope.update(resolved)
    if not McpRoleChecker(service).check_permission(handler, user, arguments):
        raise AuthorizationError("Insufficient permissions")


# Body whitespace is content; do not normalize it at the facade boundary.
class WikiChange(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppendWiki(WikiChange):
    action: Literal["append"]
    text: Annotated[str, Field(min_length=1, max_length=32000)]


class PatchWiki(WikiChange):
    action: Literal["patch"]
    edits: Annotated[list[WikiTextEdit], Field(min_length=1, max_length=20)]


class ReplaceWiki(WikiChange):
    action: Literal["replace"]
    content: Annotated[str, Field(max_length=32000)]


class DeleteWiki(WikiChange):
    action: Literal["delete"]


class SecretSelectionWiki(WikiChange):
    action: Literal["secret_selection"]
    name: Annotated[str, Field(min_length=1, max_length=256)]
    selection: WikiSecretSelection


class WikiSecretReferenceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str
    name: str
    scope: Literal["project"]
    scope_uid: str
    state: Literal["active"]
    revision: int


WikiMutation = Annotated[
    AppendWiki | PatchWiki | ReplaceWiki | DeleteWiki | SecretSelectionWiki, Field(discriminator="action")
]
WIKI_ACTION_COMMANDS = {
    "append": "append_wiki_content",
    "patch": "patch_wiki_content",
    "replace": "replace_wiki_content",
    "delete": "delete_project_wiki",
    "secret_selection": "convert_wiki_selection_to_secret",
}


class WikiActionOutput(CommandOutput):
    wiki_uid: str | None = None
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    deleted: Literal[True] | None = None
    reference: WikiSecretReferenceMetadata | None = None
    editor_generation: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def require_one_result(self):
        changed = self.wiki_uid is not None and self.revision is not None and self.deleted is None
        deleted = self.deleted is True and self.wiki_uid is None and self.revision is None
        if not (changed or deleted):
            raise ValueError("Expected one wiki mutation result")
        return self

    @model_serializer(mode="wrap")
    def serialize_result(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


@McpTool.add(
    "user",
    description="Append, patch, replace, delete, or convert a literal stored Markdown UTF16 selection to a masked project secret reference on one revision-reviewed wiki. Preserve content; deletion requires explicit user intent.",
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def update_wiki(
    project_uid: str,
    wiki_uid: str,
    expected_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
    change: WikiMutation,
    user: User,
    service: DomainService,
) -> WikiActionOutput:
    command = WIKI_ACTION_COMMANDS[change.action]
    arguments = {
        "project_uid": project_uid,
        "wiki_uid": wiki_uid,
        "expected_revision": expected_revision,
        **change.model_dump(exclude={"action"}),
    }
    if isinstance(change, SecretSelectionWiki):
        arguments["selection"] = change.selection
    if isinstance(change, PatchWiki):
        arguments["edits"] = change.edits
    with native_owner_boundary(command, arguments):
        _authorize_command(command, user, service, arguments, "Native wiki command is unavailable")
        if isinstance(change, SecretSelectionWiki):
            try:
                result = service.secret_reference.convert_wiki_selection(
                    user,
                    project_uid,
                    wiki_uid,
                    expected_revision,
                    change.name,
                    change.selection,
                    collaboration_check=lambda generation: _check_conversion_owner(wiki_uid, generation),
                )
            except WikiSecretSelectionError as error:
                raise ValidationError(error.code) from None
        else:
            repository = NativeWikiRepository(user, service)
            try:
                if isinstance(change, AppendWiki):
                    result = append_wiki(repository, project_uid, wiki_uid, expected_revision, change.text)
                elif isinstance(change, PatchWiki):
                    result = patch_wiki(
                        repository,
                        project_uid,
                        wiki_uid,
                        expected_revision,
                        [(edit.old_text, edit.new_text) for edit in change.edits],
                    )
                elif isinstance(change, ReplaceWiki):
                    result = replace_wiki(repository, project_uid, wiki_uid, expected_revision, change.content)
                else:
                    result = delete_wiki(repository, project_uid, wiki_uid, expected_revision)
            except (WikiContentConflict, WikiValidationError, ValueError) as exc:
                if isinstance(change, AppendWiki) and not isinstance(exc, (WikiContentConflict, WikiValidationError)):
                    raise
                raise ValidationError(str(exc)) from exc
        if change.action == "secret_selection":
            return WikiActionOutput.model_validate(result)
        output = DeletedOutput if change.action == "delete" else WikiRevisionOutput
        return WikiActionOutput.model_validate(output.model_validate(result).model_dump())
