"""Canonical work commands share native owners without primitive redispatch."""

from typing import Annotated, Literal
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardVerification import VerificationSubmission
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.security import RoleFinder
from pydantic import BaseModel, ConfigDict, Field
from ..mcp_integration.RoleFilter import McpRoleFilter
from ..mcp_integration.Tool import McpTool
from ..mcp_integration.WorkOutputs import WorkActionOutput
from . import CardMcp


class TimerChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["start", "pause", "resume", "stop", "complete"]
    checkitem_uid: str = Field(min_length=1, max_length=64, pattern=r"\S")
    replace_active: bool = False


class RecordVerification(VerificationSubmission):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["record_verification"]


WorkChange = Annotated[TimerChange | RecordVerification, Field(discriminator="action")]


@McpTool.add(
    "user", description="Change my checklist work timer or append reviewer evidence for the current card revision."
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
def work(
    project_uid: str,
    card_uid: str,
    change: WorkChange,
    user: User,
    service: DomainService,
) -> WorkActionOutput:
    """Use the same authorized actor and service for one native work change."""
    if isinstance(change, TimerChange):
        return WorkActionOutput.model_validate(
            CardMcp._change_card_checkitem_work(
                project_uid, card_uid, change.checkitem_uid, change.action, user, service, change.replace_active
            )
        )
    return WorkActionOutput.model_validate(
        CardMcp._record_card_verification_evidence(
            project_uid,
            card_uid,
            change.expected_change_seq,
            change.decision,
            change.evidence,
            user,
            service,
            change.expected_record_uid,
            change.required_checkitem_uids,
        )
    )
