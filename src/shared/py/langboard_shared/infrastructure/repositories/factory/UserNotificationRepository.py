from dataclasses import dataclass
from datetime import timedelta
from json import dumps, loads
from typing import Literal
from dateutil.relativedelta import relativedelta
from sqlalchemy import BigInteger, String, and_, case, false, func, literal, literal_column, or_, select, true
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime
from ....core.types.ParamTypes import TUserParam
from ....domain.models import (
    Card,
    CardComment,
    Checkitem,
    Checklist,
    Project,
    ProjectAssignedUser,
    ProjectInvitation,
    ProjectWiki,
    ProjectWikiAssignedUser,
    UserNotification,
)
from ....domain.models.UserNotification import NotificationInboxState, NotificationType
from ....domain.services.CardVisibilityPolicy import CardVisibilityContext, card_visibility_scope
from ....helpers import InfraHelper


@dataclass(frozen=True)
class NotificationThreadRow:
    event_id: int
    created_at: SafeDateTime
    card_id: int | None
    unread_events: int
    unread_mention: bool
    latest_type: NotificationType
    project_id: int | None
    card_title: str | None
    observed_unread_event_ids: tuple[int, ...]
    observed_state_event_ids: tuple[int, ...]
    state_event_count: int


class UserNotificationRepository(BaseRepository[UserNotification]):
    @staticmethod
    def model_cls():
        return UserNotification

    @staticmethod
    def name() -> str:
        return "user_notification"

    def _scoped_notifications(
        self, user: TUserParam, time_range: Literal["3d", "7d", "1m", "all"],
        contexts: dict[int, CardVisibilityContext],
    ):
        user_id = InfraHelper.convert_id(user)
        dialect = DbEngine.get_main_engine().dialect.name
        if dialect == "sqlite":
            refs = func.json_each(UserNotification.record_list).table_valued("key", "value").alias("notification_refs")
            table = func.json_extract(refs.c.value, "$[0]")
            record_id = func.json_extract(refs.c.value, "$[1]")
        elif dialect == "postgresql":
            refs = func.jsonb_array_elements(sql_cast(UserNotification.record_list, JSONB)).table_valued("value").alias("notification_refs")
            table = refs.c.value.op("->>")(0)
            record_id = sql_cast(refs.c.value.op("->>")(1), BigInteger)
        elif dialect in {"mysql", "mariadb"}:
            refs = func.JSON_TABLE(
                UserNotification.record_list,
                literal_column("'$[*]' COLUMNS (ref_table VARCHAR(64) PATH '$[0]', ref_id BIGINT PATH '$[1]')"),
            ).table_valued("ref_table", "ref_id").alias("notification_refs")
            table, record_id = refs.c.ref_table, refs.c.ref_id
        else:
            # Unimplemented dialects must not fall back to an unscoped list.
            return None
        grouped: dict[CardVisibilityContext, list[int]] = {}
        for project_id, context in contexts.items():
            grouped.setdefault(context, []).append(project_id)
        card_scope = or_(false(), *(
            Card.project_id.in_(ids) & card_visibility_scope(context)
            for context, ids in grouped.items()
        ))
        def visible_card(card_id):
            return select(Card.id).where(Card.id == card_id, Card.deleted_at.is_(None), card_scope).correlate_except(Card).exists()
        project_visible = select(Project.id).where(
            Project.id == record_id, Project.deleted_at.is_(None),
            or_(Project.id.in_(list(contexts)), UserNotification.notification_type == NotificationType.ProjectInvited),
        ).correlate(refs, UserNotification).exists()
        allowed = [and_(table == "project", project_visible), and_(table == "card", visible_card(record_id))]
        for model in (CardComment, Checklist):
            allowed.append(and_(table == model.__tablename__, select(model.id).where(
                model.id == record_id, model.deleted_at.is_(None), visible_card(model.card_id),
            ).correlate(refs).exists()))
        allowed.append(and_(table == "checkitem", select(Checkitem.id).join(Checklist, Checklist.id == Checkitem.checklist_id).where(
            Checkitem.id == record_id, Checkitem.deleted_at.is_(None), Checklist.deleted_at.is_(None), visible_card(Checklist.card_id),
        ).correlate(refs).exists()))
        wiki_assigned = select(ProjectWikiAssignedUser.id).where(
            ProjectWikiAssignedUser.project_wiki_id == ProjectWiki.id, ProjectWikiAssignedUser.user_id == user_id,
        ).exists()
        wiki_visible = select(ProjectWiki.id).join(Project, Project.id == ProjectWiki.project_id).where(
            ProjectWiki.id == record_id, ProjectWiki.deleted_at.is_(None), ProjectWiki.project_id.in_(list(contexts)),
            or_(ProjectWiki.is_public.is_(True), Project.owner_id == user_id, wiki_assigned),
        ).correlate(refs).exists()
        allowed.append(and_(table == "project_wiki", wiki_visible))
        invitation_visible = select(ProjectInvitation.id).join(Project, Project.id == ProjectInvitation.project_id).where(
            ProjectInvitation.id == record_id, Project.deleted_at.is_(None),
            UserNotification.notification_type == NotificationType.ProjectInvited,
        ).correlate(refs, UserNotification).exists()
        allowed.append(and_(table == "project_invitation", invitation_visible))
        invalid_reference = select(literal(1)).select_from(refs).where(~func.coalesce(or_(*allowed), false())).correlate(UserNotification).exists()
        has_reference = select(literal(1)).select_from(refs).correlate(UserNotification).exists()
        def scoped(query):
            query = query.where(UserNotification.receiver_id == user_id, UserNotification.web_visible.is_(True),
                                has_reference, ~invalid_reference)
            if time_range.endswith("d"):
                query = query.where(UserNotification.created_at >= SafeDateTime.now() - timedelta(days=int(time_range[:-1])))
            elif time_range.endswith("m"):
                query = query.where(UserNotification.created_at >= SafeDateTime.now() - relativedelta(months=int(time_range[:-1])))
            return query
        return scoped, refs, table, record_id

    def get_scoped_list(
        self, user: TUserParam, time_range: Literal["3d", "7d", "1m", "all"],
        page: int, limit: int, unread_only: bool, *, contexts: dict[int, CardVisibilityContext],
    ) -> tuple[list[UserNotification], int]:
        """Authorize every JSON reference before notification pages and unread counts."""
        scope = self._scoped_notifications(user, time_range, contexts)
        if scope is None:
            return [], 0
        scoped, _, _, _ = scope
        with DbSession.use(readonly=False) as db:
            unread = db.exec(scoped(SqlBuilder.select.count(UserNotification, UserNotification.id)).where(
                UserNotification.read_at.is_(None),
            )).first() or 0
            query = scoped(SqlBuilder.select.table(UserNotification))
            if unread_only:
                query = query.where(UserNotification.read_at.is_(None))
            return list(db.exec(query.order_by(UserNotification.created_at.desc(), UserNotification.id.desc()).limit(
                limit + 1,
            ).offset((page - 1) * limit)).all()), unread

    def get_scoped_threads(
        self, user: TUserParam, limit: int, *, contexts: dict[int, CardVisibilityContext],
        before: tuple[SafeDateTime, int] | None = None, unread_only: bool = False,
        state: NotificationInboxState = "inbox", project_id: int | None = None, reason: NotificationType | None = None,
    ) -> tuple[list[NotificationThreadRow], int]:
        """Return the latest authorized event per card and the unread thread count."""
        if not 1 <= limit <= 100:
            raise ValueError("Thread page limit must be between 1 and 100")
        scope = self._scoped_notifications(user, "all", contexts)
        if scope is None:
            return [], 0
        scoped, refs, table, record_id = scope

        def reference_id(kind, value, source):
            return (
                select(func.min(value)).select_from(source).where(table == kind)
                .correlate(UserNotification).scalar_subquery()
            )

        card_id = func.coalesce(
            reference_id("card", record_id, refs),
            reference_id("card_comment", CardComment.card_id, refs.join(CardComment, CardComment.id == record_id)),
            reference_id("checklist", Checklist.card_id, refs.join(Checklist, Checklist.id == record_id)),
            reference_id("checkitem", Checklist.card_id, refs.join(Checkitem, Checkitem.id == record_id)
                           .join(Checklist, Checklist.id == Checkitem.checklist_id)),
        )
        event_project = func.coalesce(
            select(Card.project_id).where(Card.id == card_id).correlate(UserNotification).scalar_subquery(),
            reference_id("project", record_id, refs),
        )
        events = scoped(select(
            UserNotification.id.label("event_id"), UserNotification.created_at,
            UserNotification.read_at, UserNotification.notification_type, card_id.label("card_id"),
            event_project.label("project_id"),
            func.coalesce(UserNotification.inbox_state, "inbox").label("inbox_state"),
        ))
        if project_id is not None:
            events = events.where(event_project == project_id)
        if reason is not None:
            events = events.where(UserNotification.notification_type == reason)
        events = events.cte("notification_thread_events")
        groups = (events.c.card_id, case((events.c.card_id.is_(None), events.c.event_id)))
        state_group = (*groups, events.c.inbox_state)
        state_order = case((events.c.inbox_state == "inbox", 0), (events.c.inbox_state == "later", 1), else_=2)
        unread = events.c.read_at.is_(None)
        sort_order = (events.c.created_at.desc(), events.c.event_id.desc())
        mention = events.c.notification_type.in_((NotificationType.MentionedInCard, NotificationType.MentionedInComment))
        ranked = select(
            events.c.event_id, events.c.created_at, events.c.card_id, events.c.notification_type, events.c.read_at,
            events.c.inbox_state, events.c.project_id,
            func.row_number().over(partition_by=groups, order_by=(state_order, *sort_order)).label("position"),
            func.count().over(partition_by=state_group).label("state_event_count"),
            func.sum(case((unread, 1), else_=0)).over(partition_by=state_group).label("unread_events"),
            func.max(case((unread & mention, 1), else_=0)).over(partition_by=state_group).label("unread_mention"),
        ).cte("ranked_notification_threads")
        threads = select(ranked).where(ranked.c.position == 1, ranked.c.inbox_state == state).cte(
            "notification_threads",
        )
        page = select(threads)
        if unread_only:
            page = page.where(threads.c.unread_events > 0)
        if before is not None:
            timestamp, event_id = before
            page = page.where(or_(
                threads.c.created_at < timestamp,
                and_(threads.c.created_at == timestamp, threads.c.event_id < event_id),
            ))
        page = page.order_by(threads.c.created_at.desc(), threads.c.event_id.desc()).limit(limit + 1).cte(
            "notification_thread_page",
        )
        totals = select(func.count().label("unread_threads")).select_from(threads).where(
            threads.c.unread_events > 0,
        ).subquery()
        aggregation = {
            "sqlite": func.json_group_array,
            "postgresql": func.json_agg,
            "mysql": func.json_arrayagg,
            "mariadb": func.json_arrayagg,
        }[DbEngine.get_main_engine().dialect.name]
        observed_ids = select(
            page.c.event_id.label("thread_event_id"),
            aggregation(ranked.c.event_id).label("state_ids"),
            aggregation(case((ranked.c.read_at.is_(None), ranked.c.event_id))).label("unread_ids"),
        ).select_from(page.join(
            ranked,
            or_(
                ranked.c.card_id == page.c.card_id,
                and_(page.c.card_id.is_(None), ranked.c.event_id == page.c.event_id),
            ),
        )).where(ranked.c.inbox_state == state).group_by(page.c.event_id).subquery()
        query = select(
            page.c.event_id, page.c.created_at, page.c.card_id, page.c.unread_events,
            page.c.unread_mention, page.c.notification_type, page.c.project_id, Card.title,
            observed_ids.c.unread_ids, observed_ids.c.state_ids, page.c.state_event_count, totals.c.unread_threads,
        ).select_from(totals.outerjoin(page, true()).outerjoin(Card, Card.id == page.c.card_id).outerjoin(
            observed_ids, observed_ids.c.thread_event_id == page.c.event_id,
        ))
        query = query.order_by(page.c.created_at.desc(), page.c.event_id.desc())
        with DbSession.use(readonly=False) as db:
            rows = db.exec(query).all()
        def event_ids(ids):
            return tuple(event_id for event_id in ((loads(ids) if isinstance(ids, str) else ids) or ()) if event_id is not None)

        return [
            NotificationThreadRow(
                event_id, created_at, card_id, unread_events, bool(unread_mention), kind, project_id, title,
                event_ids(unread_ids), event_ids(state_ids), state_count,
            )
            for (event_id, created_at, card_id, unread_events, unread_mention, kind, project_id, title,
                 unread_ids, state_ids, state_count, _) in rows
            if event_id is not None
        ], rows[0][-1]

    def read_scoped_events(
        self, user: TUserParam, event_ids: set[int], *, contexts: dict[int, CardVisibilityContext],
    ) -> int:
        """Read only explicitly observed events, even if older events commit late."""
        return self._update_scoped_events(
            user, event_ids, contexts, UserNotification.read_at.is_(None), read_at=SafeDateTime.now(),
        )

    def set_scoped_event_state(
        self, user: TUserParam, event_ids: set[int], expected: NotificationInboxState,
        target: NotificationInboxState, *, contexts: dict[int, CardVisibilityContext],
    ) -> int:
        if expected == target:
            return 0
        expected_state = UserNotification.inbox_state.is_(None) if expected == "inbox" else UserNotification.inbox_state == expected
        return self._update_scoped_events(
            user, event_ids, contexts, expected_state, inbox_state=None if target == "inbox" else target,
        )

    def _update_scoped_events(
        self, user: TUserParam, event_ids: set[int], contexts: dict[int, CardVisibilityContext],
        expected_condition, **values,
    ) -> int:
        if not event_ids:
            raise ValueError("An observed event set is required")
        scope = self._scoped_notifications(user, "all", contexts)
        if scope is None:
            return 0
        scoped, _, _, _ = scope
        # Bind the observed IDs once so large threads fit the same atomic update
        # without exceeding a database driver's bind parameter limit.
        encoded_ids = dumps(sorted(event_ids), separators=(",", ":"))
        dialect = DbEngine.get_main_engine().dialect.name
        if dialect == "sqlite":
            ids = func.json_each(encoded_ids).table_valued("value")
        elif dialect == "postgresql":
            ids = func.jsonb_array_elements_text(sql_cast(literal(encoded_ids), JSONB)).table_valued("value")
        else:
            ids = func.JSON_TABLE(
                encoded_ids, literal_column("'$[*]' COLUMNS (value BIGINT PATH '$')"),
            ).table_valued("value")
        observed = select(sql_cast(ids.c.value, BigInteger))
        # LIMIT prevents MySQL from merging the self-reference into the target.
        allowed = scoped(select(UserNotification.id)).where(UserNotification.id.in_(observed)).limit(
            len(event_ids),
        ).subquery()
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.update.table(UserNotification)
                .where(UserNotification.id.in_(select(allowed.c.id)), expected_condition)
                .values(**values)
            )

    def get_list(
        self,
        user: TUserParam,
        time_range: Literal["3d", "7d", "1m", "all"] = "3d",
        page: int = 1,
        limit: int = 20,
        unread_only: bool = False,
        authorized_projects_only: bool = False,
    ):
        """Return one ordered notification page for a user."""

        user_id = InfraHelper.convert_id(user)
        query = (
            SqlBuilder.select.table(UserNotification)
            .where(UserNotification.column("receiver_id") == user_id)
            .where(UserNotification.column("web_visible") == True)  # noqa: E712
        )

        if unread_only:
            query = query.where(UserNotification.column("read_at") == None)  # noqa

        if authorized_projects_only:
            record_list_text = sql_cast(UserNotification.column("record_list"), String)
            project_id_text = sql_cast(ProjectAssignedUser.column("project_id"), String)
            authorized_project = (
                select(ProjectAssignedUser.column("id"))
                .join(Project, ProjectAssignedUser.column("project_id") == Project.column("id"))
                .where(ProjectAssignedUser.column("user_id") == user_id)
                .where(Project.column("deleted_at") == None)  # noqa: E711
                .where(
                    or_(
                        record_list_text.like(literal('%["project", ') + project_id_text + literal("]%")),
                        record_list_text.like(literal('%["project",') + project_id_text + literal("]%")),
                    )
                )
                .exists()
            )
            query = query.where(
                or_(
                    UserNotification.column("notification_type") == NotificationType.ProjectInvited,
                    authorized_project,
                )
            )

        if time_range.endswith("d"):
            days = int(time_range[:-1])
            query = query.where(UserNotification.column("created_at") >= SafeDateTime.now() - timedelta(days=days))
        elif time_range.endswith("m"):
            month = int(time_range[:-1])
            query = query.where(
                UserNotification.column("created_at") >= SafeDateTime.now() - relativedelta(months=month)
            )

        query = query.order_by(
            UserNotification.column("created_at").desc(),
            UserNotification.column("id").desc(),
        )
        query = query.limit(limit + 1).offset((page - 1) * limit)

        notifications = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(query)
            notifications = result.all()

        return notifications

    def get_project_invitation_notification(self, user: TUserParam, record_list: str) -> UserNotification | None:
        user_id = InfraHelper.convert_id(user)
        notification = None
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(UserNotification)
                .where(
                    (UserNotification.column("receiver_id") == user_id)
                    & (UserNotification.column("notification_type") == NotificationType.ProjectInvited)
                    & (sql_cast(UserNotification.column("record_list"), String) == record_list)
                )
                .limit(1)
            )
            notification = result.first()
        return notification

    def get_pending_web_fanout(self, older_than: SafeDateTime, limit: int) -> list[UserNotification]:
        query = SqlBuilder.select.table(UserNotification).where((UserNotification.column('web_fanout_pending').is_(True)) & (UserNotification.column('updated_at') <= older_than)).order_by(UserNotification.column('updated_at'), UserNotification.column('id')).limit(limit)
        with DbSession.use(readonly=False) as db:
            return db.exec(query).all()

    def defer_web_fanout(self, notification: UserNotification) -> None:
        with DbSession.use(readonly=False) as db:
            db.exec(SqlBuilder.update.table(UserNotification).values({UserNotification.column('updated_at'): SafeDateTime.now()}).where((UserNotification.column('id') == notification.id) & (UserNotification.column('web_fanout_pending').is_(True))))

    def complete_web_fanout(self, notification: UserNotification) -> None:
        with DbSession.use(readonly=False) as db:
            db.exec(SqlBuilder.update.table(UserNotification).values({UserNotification.column('web_fanout_pending'): False}).where(UserNotification.column('id') == notification.id))

    def get_pending_work_events(self, limit: int = 100) -> list[UserNotification]:
        """Read a bounded durable work-event backlog from the primary database."""

        eligible_types = [
            NotificationType.AssignedToCard,
            NotificationType.MentionedInCard,
            NotificationType.MentionedInComment,
            NotificationType.MentionedInWiki,
            NotificationType.NotifiedFromChecklist,
            NotificationType.ProjectInvited,
            NotificationType.ScheduledRule,
            NotificationType.WorkflowStageEntered,
        ]
        from ....domain.models.AppNotificationSubscription import AppNotificationSubscription
        subscribed = select(AppNotificationSubscription.id).where(
            AppNotificationSubscription.user_id == UserNotification.receiver_id,
            AppNotificationSubscription.state == "enabled",
            AppNotificationSubscription.created_at <= UserNotification.created_at,
        ).exists()
        activity = and_(UserNotification.notification_type.in_([
            NotificationType.CardCommentAdded, NotificationType.CardMoved]), subscribed)
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.select.table(UserNotification)
                .where(or_(UserNotification.column("notification_type").in_(eligible_types), activity))
                .where(UserNotification.column("work_event_dispatched_at") == None)  # noqa: E711
                .order_by(UserNotification.column("created_at").asc(), UserNotification.column("id").asc())
                .limit(max(1, min(limit, 100)))
            ).all()

    def mark_work_event_dispatched(self, notification: UserNotification) -> None:
        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.update.table(UserNotification)
                .values(work_event_dispatched_at=SafeDateTime.now())
                .where(UserNotification.column("id") == notification.id)
                .where(UserNotification.column("work_event_dispatched_at") == None)  # noqa: E711
            )

    def count_unread(
        self, user: TUserParam, time_range: Literal["3d", "7d", "1m", "all"] = "all",
        *, readonly: bool = True,
    ) -> int:
        user_id = InfraHelper.convert_id(user)
        query = SqlBuilder.select.count(UserNotification, UserNotification.column("id")).where(
            (UserNotification.column("receiver_id") == user_id)
            & (UserNotification.column("web_visible") == True)  # noqa: E712
            & (UserNotification.column("read_at") == None)  # noqa: E711
        )
        if time_range.endswith("d"):
            query = query.where(
                UserNotification.column("created_at") >= SafeDateTime.now() - timedelta(days=int(time_range[:-1]))
            )
        elif time_range.endswith("m"):
            query = query.where(
                UserNotification.column("created_at") >= SafeDateTime.now() - relativedelta(months=int(time_range[:-1]))
            )

        with DbSession.use(readonly=readonly) as db:
            result = db.exec(query)
            return result.first() or 0

    def get_mentioned_card_ids(self, user: TUserParam, limit: int = 500) -> list[int]:
        """Return recent card ids mentioned in notifications for one receiver."""

        user_id = InfraHelper.convert_id(user)
        notifications = []
        with DbSession.use(readonly=True) as db:
            notifications = db.exec(
                SqlBuilder.select.table(UserNotification)
                .where(UserNotification.column("receiver_id") == user_id)
                .where(UserNotification.column("web_visible") == True)  # noqa: E712
                .where(
                    UserNotification.column("notification_type").in_(
                        [NotificationType.MentionedInCard, NotificationType.MentionedInComment]
                    )
                )
                .order_by(UserNotification.column("created_at").desc(), UserNotification.column("id").desc())
                .limit(max(1, min(limit, 500)))
            ).all()

        card_ids: list[int] = []
        for notification in notifications:
            for table_name, record_id in notification.record_list:
                if table_name == "card":
                    card_ids.append(record_id)
        return card_ids

    def read_all_by_user(self, user: TUserParam, read_at: SafeDateTime | None = None):
        user_id = InfraHelper.convert_id(user)
        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.update.table(UserNotification)
                .values({UserNotification.column("read_at"): read_at or SafeDateTime.now()})
                .where(
                    (UserNotification.column("receiver_id") == user_id)
                    & (UserNotification.column("web_visible") == True)  # noqa: E712
                    & (UserNotification.column("read_at") == None)  # noqa: E711
                )
            )

    def delete_all(self, user: TUserParam):
        user_id = InfraHelper.convert_id(user)
        with DbSession.use(readonly=False) as db:
            db.exec(SqlBuilder.delete.table(UserNotification).where(UserNotification.column("receiver_id") == user_id))

    def delete_all_by_ids(self, notification_ids: list[int]):
        if not notification_ids:
            return
        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.delete.table(UserNotification).where(UserNotification.column("id").in_(notification_ids))
            )
