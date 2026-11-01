from ...core.db import ApiField, SnowflakeIDField
from ...core.types import SnowflakeID
from ..constants.DomainEventType import DomainEventType
from .bases import BaseBotScopeModel
from .Project import Project


class ProjectBotScope(BaseBotScopeModel, table=True):
    project_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Project, nullable=False, index=True, api_field=ApiField(name="project_uid")
    )

    @staticmethod
    def get_available_conditions() -> set[DomainEventType]:
        return {
            DomainEventType.ProjectUpdated,
            DomainEventType.ProjectDeleted,
            DomainEventType.ProjectLabelCreated,
            DomainEventType.ProjectLabelUpdated,
            DomainEventType.ProjectLabelDeleted,
            DomainEventType.ProjectWikiCreated,
            DomainEventType.ProjectWikiUpdated,
            DomainEventType.ProjectWikiPublicityChanged,
            DomainEventType.ProjectWikiDeleted,
            DomainEventType.ProjectColumnCreated,
            DomainEventType.ProjectColumnNameChanged,
            DomainEventType.ProjectColumnDeleted,
            DomainEventType.CardCreated,
            DomainEventType.CardUpdated,
            DomainEventType.CardMoved,
            DomainEventType.CardLabelsUpdated,
            DomainEventType.CardRelationshipsUpdated,
            DomainEventType.CardDeleted,
            DomainEventType.CardAttachmentUploaded,
            DomainEventType.CardAttachmentNameChanged,
            DomainEventType.CardAttachmentDeleted,
            DomainEventType.CardCommentAdded,
            DomainEventType.CardCommentUpdated,
            DomainEventType.CardCommentDeleted,
            DomainEventType.CardCommentReacted,
            DomainEventType.CardCommentUnreacted,
            DomainEventType.CardChecklistCreated,
            DomainEventType.CardChecklistTitleChanged,
            DomainEventType.CardChecklistChecked,
            DomainEventType.CardChecklistUnchecked,
            DomainEventType.CardChecklistDeleted,
            DomainEventType.CardCheckitemCreated,
            DomainEventType.CardCheckitemTitleChanged,
            DomainEventType.CardCheckitemTimerStarted,
            DomainEventType.CardCheckitemTimerPaused,
            DomainEventType.CardCheckitemTimerStopped,
            DomainEventType.CardCheckitemChecked,
            DomainEventType.CardCheckitemUnchecked,
            DomainEventType.CardCheckitemCardified,
            DomainEventType.CardCheckitemDeleted,
        }

    @staticmethod
    def get_scope_column_name() -> str:
        return "project_id"
