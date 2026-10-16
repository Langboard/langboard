from typing import Sequence
from ...core.broker import Broker
from ...domain.models import Bot, Card, Project, ProjectActivity, User
from ...domain.models.ProjectActivity import ProjectActivityType
from .UserActivityTask import record_project_activity
from .utils import ActivityTaskHelper


@Broker.wrap_async_task_decorator
async def card_relationship_updated(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    old_relationship_ids: Sequence[int],
    new_relationship_ids: Sequence[int],
    is_parent: bool,
):
    helper = ActivityTaskHelper(ProjectActivity)
    removed_relationships, added_relationships = helper.get_updated_card_relationships(
        old_relationship_ids, new_relationship_ids, is_parent
    )
    if not removed_relationships and not added_relationships:
        return

    activity_history = {
        **helper.create_project_default_history(project, card=card),
        "removed_relationships": removed_relationships,
        "added_relationships": added_relationships,
    }
    activity = helper.record(
        user_or_bot,
        activity_history,
        **helper.get_card_activity_params(ProjectActivityType.CardRelationshipsUpdated, project, card),
    )
    record_project_activity(user_or_bot, activity)
