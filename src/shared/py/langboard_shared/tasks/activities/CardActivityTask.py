from typing import Any, Sequence
from ...core.broker import Broker
from ...domain.models import Bot, Card, Project, ProjectActivity, ProjectColumn, User
from ...domain.models.ProjectActivity import ProjectActivityType
from .UserActivityTask import record_project_activity
from .utils import ActivityHistoryHelper, ActivityTaskHelper


@Broker.wrap_async_task_decorator
async def card_created(user_or_bot: User | Bot, project: Project, card: Card):
    helper = ActivityTaskHelper(ProjectActivity)
    activity_history = helper.create_project_default_history(project, card=card)
    activity = helper.record(
        user_or_bot, activity_history, **helper.get_card_activity_params(ProjectActivityType.CardCreated, project, card)
    )
    record_project_activity(user_or_bot, activity)


@Broker.wrap_async_task_decorator
async def card_updated(user_or_bot: User | Bot, project: Project, old_dict: dict[str, Any], card: Card):
    helper = ActivityTaskHelper(ProjectActivity)
    activity_history = {
        **helper.create_project_default_history(project, card=card),
        **ActivityHistoryHelper.create_changes(old_dict, card),
    }
    activity = helper.record(
        user_or_bot, activity_history, **helper.get_card_activity_params(ProjectActivityType.CardUpdated, project, card)
    )
    record_project_activity(user_or_bot, activity)


@Broker.wrap_async_task_decorator
async def card_moved(
    user_or_bot: User | Bot,
    project: Project,
    card: Card,
    from_column: ProjectColumn,
):
    helper = ActivityTaskHelper(ProjectActivity)
    activity_history = {
        **helper.create_project_default_history(project, card=card),
        "from_column": ActivityHistoryHelper.create_project_column_history(from_column),
    }
    activity = helper.record(
        user_or_bot, activity_history, **helper.get_card_activity_params(ProjectActivityType.CardMoved, project, card)
    )
    record_project_activity(user_or_bot, activity)


@Broker.wrap_async_task_decorator
async def card_assigned_users_updated(
    user_or_bot: User | Bot, project: Project, card: Card, old_user_ids: Sequence[int], new_user_ids: Sequence[int]
):
    if isinstance(old_user_ids, str) or isinstance(new_user_ids, str):
        return  # Invalid input, should be sequences of integers

    helper = ActivityTaskHelper(ProjectActivity)
    removed_users, added_users = helper.get_updated_users(old_user_ids, new_user_ids)
    if not removed_users and not added_users:
        return

    activity_history = {
        **helper.create_project_default_history(project, card=card),
        "removed_users": removed_users,
        "added_users": added_users,
    }
    activity = helper.record(
        user_or_bot,
        activity_history,
        **helper.get_card_activity_params(ProjectActivityType.CardAssignedUsersUpdated, project, card),
    )
    record_project_activity(user_or_bot, activity)


@Broker.wrap_async_task_decorator
async def card_labels_updated(
    user_or_bot: User | Bot, project: Project, card: Card, old_label_ids: Sequence[int], new_label_ids: Sequence[int]
):
    helper = ActivityTaskHelper(ProjectActivity)
    removed_labels, added_labels = helper.get_updated_labels(old_label_ids, new_label_ids)
    if not removed_labels and not added_labels:
        return

    activity_history = {
        **helper.create_project_default_history(project, card=card),
        "removed_labels": removed_labels,
        "added_labels": added_labels,
    }
    activity = helper.record(
        user_or_bot, activity_history, **helper.get_card_activity_params(ProjectActivityType.CardLabelsUpdated, project, card)
    )
    record_project_activity(user_or_bot, activity)


@Broker.wrap_async_task_decorator
async def card_deleted(user_or_bot: User | Bot, project: Project, card: Card):
    helper = ActivityTaskHelper(ProjectActivity)
    activity_history = helper.create_project_default_history(project, card=card)
    activity = helper.record(
        user_or_bot, activity_history, **helper.get_card_activity_params(ProjectActivityType.CardDeleted, project, card)
    )
    record_project_activity(user_or_bot, activity)


def record_card_linked(user: User, project: Project, mirror: Card, source_project: Project, source: Card):
    """Persist only source identity, never private source content, with the reference transaction."""
    helper = ActivityTaskHelper(ProjectActivity)
    return helper.record(
        user,
        {"source": {"type": Card.LINKED_RESOURCE_PROJECT_CARD, "uid": source.get_uid(),
                    "project_uid": source_project.get_uid(),
                    "url": f"/board/{source_project.get_uid()}/{source.get_uid()}"}},
        **helper.get_card_activity_params(ProjectActivityType.CardLinked, project, mirror),
    )


def record_card_cloned(user, project, clone, source_project, source, *, request_uid, identity, excluded):
    """The clone starts with original address provenance and a persisted retry receipt."""
    helper = ActivityTaskHelper(ProjectActivity)
    return helper.record(
        user,
        {
            **helper.create_project_default_history(project, card=clone),
            "source": {
                "type": Card.LINKED_RESOURCE_PROJECT_CARD,
                "uid": source.get_uid(),
                "project_uid": source_project.get_uid(),
                "url": f"/board/{source_project.get_uid()}/{source.get_uid()}",
            },
            "clone_request_uid": request_uid,
            "clone_identity": identity,
            "excluded": excluded,
        },
        **helper.get_card_activity_params(ProjectActivityType.CardCloned, project, clone),
    )


def record_card_cross_board_moved(user, source_project, destination, card, from_column,
                                 *, request_uid, identity, excluded):
    """Persist a retry receipt with the card and a content-free source-board event."""
    helper = ActivityTaskHelper(ProjectActivity)
    history = {
        **helper.create_project_default_history(destination, card=card),
        "source": {"project_uid": source_project.get_uid(), "column_uid": from_column.get_uid()},
        "destination": {"project_uid": destination.get_uid(), "column_uid": card.project_column_id.to_short_code()},
        "move_request_uid": request_uid,
        "move_identity": identity,
        "excluded": excluded,
    }
    receipt = helper.record(user, history, **helper.get_card_activity_params(ProjectActivityType.CardMoved, destination, card))
    # A source feed must never retain a body/title or destination identity after readers lose access.
    helper.record(user, {"moved_out": True}, project_id=source_project.id,
                  project_column_id=from_column.id, activity_type=ProjectActivityType.CardMoved)
    return receipt
