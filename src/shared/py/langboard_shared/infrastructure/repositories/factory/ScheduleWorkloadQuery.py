"""One bounded primary query; no timer, notification or card bodies leave SQL."""

from sqlalchemy import BigInteger, Text, and_, case, cast, false, func, literal, literal_column, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB
from ....core.db import DbSession
from ....core.db.DbEngine import DbEngine
from ....domain.models import (
    Card,
    CardAppOwnership,
    CardAssignedUser,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    UserNotification,
    WorkflowStageDefinition,
)
from ....domain.models.Checkitem import CheckitemStatus
from ....domain.models.UserNotification import NotificationType
from ....domain.services.CardVisibilityPolicy import card_visibility_scope_by_project
from ....helpers import InfraHelper


def _personal(user_id, dialect):
    assigned = (
        select(CardAssignedUser.id)
        .where(
            CardAssignedUser.card_id == Card.id,
            CardAssignedUser.user_id == user_id,
        )
        .exists()
    )
    # Match Schedule's native assigned/created/mentioned selection without materializing history.
    if dialect == "sqlite":
        refs = func.json_each(UserNotification.record_list).table_valued("key", "value").alias("workload_refs")
        ref_table = func.json_extract(refs.c.value, "$[0]")
        ref_id = func.json_extract(refs.c.value, "$[1]")
    elif dialect == "postgresql":
        refs = (
            func.jsonb_array_elements(cast(UserNotification.record_list, JSONB))
            .table_valued("value")
            .alias("workload_refs")
        )
        ref_table = refs.c.value.op("->>")(0)
        ref_id = cast(refs.c.value.op("->>")(1), BigInteger)
    elif dialect in {"mysql", "mariadb"}:
        refs = (
            func.JSON_TABLE(
                UserNotification.record_list,
                literal_column("'$[*]' COLUMNS (ref_table VARCHAR(64) PATH '$[0]', ref_id BIGINT PATH '$[1]')"),
            )
            .table_valued("ref_table", "ref_id")
            .alias("workload_refs")
        )
        ref_table, ref_id = refs.c.ref_table, refs.c.ref_id
    else:
        raise ValueError("Unsupported database dialect for Schedule workload")
    mentioned = (
        select(ref_id)
        .select_from(UserNotification)
        .join(refs, true())
        .where(
            UserNotification.receiver_id == user_id,
            UserNotification.web_visible.is_(True),
            UserNotification.notification_type.in_(
                [NotificationType.MentionedInCard, NotificationType.MentionedInComment]
            ),
            ref_table == "card",
        )
    )
    return or_(assigned, Card.created_by_user_id == user_id, Card.id.in_(mentioned))


def schedule_workload(user, start, end, as_of, *, contexts, personal):
    """Snapshot workload independently of deadlines; all-worker time clipped to [start,min(end,as_of)).

    Missing evidence never becomes an authoritative zero. Native new stopped items with
    zero accumulation and no events are the sole no-history zero case. Deleted items and
    checklists do not constitute readable native history and are excluded, like tracking.
    """
    dialect = DbEngine.get_main_engine().dialect.name
    if dialect not in {"sqlite", "postgresql", "mysql", "mariadb"}:
        raise ValueError("Unsupported database dialect for Schedule workload")
    user_id = InfraHelper.convert_id(user)
    membership = (
        select(ProjectAssignedUser.id)
        .where(
            ProjectAssignedUser.project_id == Project.id,
            ProjectAssignedUser.user_id == user_id,
        )
        .exists()
    )
    actions = "," + cast(ProjectRole.actions, Text) + ","
    grant = (
        select(ProjectRole.id)
        .where(
            ProjectRole.project_id == Project.id,
            ProjectRole.user_id == user_id,
            or_(actions.contains(",*,"), actions.contains(",read,")),
        )
        .exists()
    )
    active_actor = (
        select(User.id)
        .where(
            User.id == user_id,
            User.deleted_at.is_(None),
            User.activated_at.is_not(None),
        )
        .exists()
    )
    # Repeat current membership/read grants within the aggregation statement itself.
    cards = (
        select(
            Card.id,
            Card.archived_at,
            ProjectColumn.is_archive,
            WorkflowStageDefinition.counts_as_completed,
            WorkflowStageDefinition.key.label("stage_key"),
        )
        .join(Project, Project.id == Card.project_id)
        .join(
            ProjectColumn,
            and_(ProjectColumn.id == Card.project_column_id, ProjectColumn.project_id == Card.project_id),
        )
        .outerjoin(WorkflowStageDefinition, WorkflowStageDefinition.key == ProjectColumn.workflow_stage)
        .where(
            active_actor,
            or_(Project.owner_id == user_id, membership & grant),
            card_visibility_scope_by_project(contexts) if contexts else false(),
            ~select(CardAppOwnership.id)
            .where(
                CardAppOwnership.card_id == Card.id,
                CardAppOwnership.app_key.is_not(None),
            )
            .exists(),
            Card.source_type.is_(None),
            Card.deleted_at.is_(None),
            Project.deleted_at.is_(None),
            ProjectColumn.deleted_at.is_(None),
        )
    )
    if personal:
        cards = cards.where(_personal(user_id, dialect))
    cards = cards.cte("workload_cards")
    items = (
        select(
            Checkitem.id,
            Checkitem.status,
            Checkitem.created_at,
            Checkitem.accumulated_seconds,
            Checkitem.is_checked,
            Checklist.card_id,
        )
        .join(Checklist, Checklist.id == Checkitem.checklist_id)
        .join(cards, cards.c.id == Checklist.card_id)
        .where(
            Checkitem.deleted_at.is_(None),
            Checklist.deleted_at.is_(None),
        )
        .cte("workload_items")
    )
    incomplete = select(items.c.id).where(items.c.card_id == cards.c.id, items.c.is_checked.is_(False)).exists()
    has_items = select(items.c.id).where(items.c.card_id == cards.c.id).exists()
    open_card = and_(
        cards.c.archived_at.is_(None),
        cards.c.is_archive.is_(False),
        or_(cards.c.counts_as_completed.is_(None), cards.c.counts_as_completed.is_(False)),
        or_(cards.c.stage_key.is_(None), cards.c.stage_key != "reference"),
    )
    workload = (
        select(
            func.count(case((open_card, 1))).label("open_count"),
            func.count(case((open_card & (~has_items | incomplete), 1))).label("incomplete_count"),
        )
        .select_from(cards)
        .cte("workload_totals")
    )
    # Keep predecessor crossings: window the complete readable history before clipping.
    order = (CheckitemTimerRecord.created_at, CheckitemTimerRecord.id)
    event_status = cast(CheckitemTimerRecord.status, Text)
    events = (
        select(
            CheckitemTimerRecord.checkitem_id.label("item_id"),
            CheckitemTimerRecord.created_at.label("at"),
            event_status.label("status"),
            func.lead(CheckitemTimerRecord.created_at, type_=CheckitemTimerRecord.created_at.type)
            .over(partition_by=CheckitemTimerRecord.checkitem_id, order_by=order)
            .label("next_at"),
            func.lag(event_status)
            .over(partition_by=CheckitemTimerRecord.checkitem_id, order_by=order)
            .label("previous_status"),
        )
        .join(items, items.c.id == CheckitemTimerRecord.checkitem_id)
        .cte("workload_events")
    )
    cutoff = min(end, as_of)
    left = case((events.c.at < start, literal(start, type_=CheckitemTimerRecord.created_at.type)), else_=events.c.at)
    right = case(
        (
            or_(events.c.next_at.is_(None), events.c.next_at > cutoff),
            literal(cutoff, type_=CheckitemTimerRecord.created_at.type),
        ),
        else_=events.c.next_at,
    )

    def duration(left_at, right_at):
        if dialect == "sqlite":
            # Native SQLite timestamps store UTC with six fractional digits. Avoid
            # julianday millisecond rounding and binary float endpoint cancellation.
            def microseconds(value):
                whole = cast(func.strftime("%s", func.substr(value, 1, 19)), BigInteger) * 1000000
                fraction = cast(func.coalesce(func.nullif(func.substr(value, 21, 6), ""), "0"), BigInteger)
                return whole + fraction

            return (microseconds(right_at) - microseconds(left_at)) / 1000000.0
        if dialect == "postgresql":
            return func.extract("epoch", right_at - left_at)
        return func.timestampdiff(literal_column("MICROSECOND"), left_at, right_at) / 1000000.0

    elapsed = duration(left, right)
    closed_seconds = duration(events.c.at, events.c.next_at)
    started = CheckitemStatus.Started.value
    valid_status = events.c.status.in_([status.value for status in CheckitemStatus])
    bad_event = or_(
        ~valid_status,
        events.c.at < items.c.created_at,
        events.c.at > as_of,
        and_(events.c.previous_status.is_(None), events.c.status != started),
        events.c.previous_status == events.c.status,
        and_(events.c.next_at.is_(None), events.c.status != cast(items.c.status, Text)),
    )
    overlaps = and_(events.c.status == started, events.c.at < cutoff, right > left)
    per_item = (
        select(
            items.c.id,
            func.count(events.c.item_id).label("event_count"),
            func.max(case((bad_event, 1), else_=0)).label("invalid"),
            func.coalesce(func.sum(case((overlaps, elapsed), else_=0.0)), 0).label("seconds"),
            func.max(case((overlaps & events.c.next_at.is_(None), 1), else_=0)).label("running"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (events.c.status == started) & events.c.next_at.is_not(None),
                            cast(func.floor(closed_seconds), BigInteger),
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("closed_seconds"),
            items.c.status,
            items.c.accumulated_seconds,
        )
        .select_from(items)
        .outerjoin(events, events.c.item_id == items.c.id)
        .group_by(
            items.c.id,
            items.c.status,
            items.c.accumulated_seconds,
        )
        .cte("workload_item_totals")
    )
    unknown = or_(
        per_item.c.invalid == 1,
        per_item.c.accumulated_seconds < 0,
        per_item.c.closed_seconds != per_item.c.accumulated_seconds,
        and_(
            per_item.c.event_count == 0,
            or_(
                per_item.c.accumulated_seconds != 0,
                per_item.c.status != CheckitemStatus.Stopped,
            ),
        ),
    )
    time = (
        select(
            func.count(per_item.c.id).label("item_count"),
            func.count(case((unknown, 1))).label("unknown_item_count"),
            func.coalesce(func.sum(case((~unknown, per_item.c.seconds), else_=0.0)), 0).label("known_invested_seconds"),
            func.coalesce(func.sum(case((~unknown, per_item.c.running), else_=0)), 0).label("running_item_count"),
        )
        .select_from(per_item)
        .cte("workload_time_totals")
    )
    statement = select(*workload.c, *time.c).select_from(workload.join(time, true()))
    with DbSession.use(readonly=False) as db:
        row = db.exec(statement).first()
    result = dict(zip((column.key for column in statement.selected_columns), row))
    result["known_invested_seconds"] = round(float(result["known_invested_seconds"]), 6)
    result["invested_seconds"] = None if result["unknown_item_count"] else result["known_invested_seconds"]
    result["running_item_count"] = None if result["unknown_item_count"] else int(result["running_item_count"])
    result["evidence"] = "incomplete" if result["unknown_item_count"] else "complete"
    return result
