from datetime import datetime
from typing import Literal, TypeAlias, TypeVar, overload, override
from sqlalchemy import String, and_, cast, literal, or_, select, tuple_, union_all
from ....core.db import DbSession, SqlBuilder
from ....core.db.queries.Select import SelectOfScalar
from ....core.domain import BaseRepository
from ....core.schema import TimeBasedPagination
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TCardParam, TColumnParam, TProjectParam, TUserOrBotParam, TUserParam, TWikiParam
from ....domain.models import (
    Bot,
    Card,
    CardComment,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWiki,
    ProjectWikiActivity,
    ProjectWikiAssignedUser,
    User,
    UserActivity,
)
from ....domain.models.bases import BaseActivityModel
from ....domain.models.ProjectActivity import ProjectActivityType
from ....domain.services.CardVisibilityPolicy import (
    CardVisibilityContext,
    card_visibility_scope,
    project_activity_visibility_scope,
)
from ....helpers import InfraHelper


_TActivityModel = TypeVar("_TActivityModel", bound=BaseActivityModel)
_TScopedActivityModel = TypeVar("_TScopedActivityModel", ProjectActivity, ProjectWikiActivity)
_TSelectParam = TypeVar("_TSelectParam")
_TActivityScope = Literal["project", "project_column", "card", "project_wiki"]
_TUserOrBotActivityParam: TypeAlias = User | Bot | SnowflakeID | int | str


class ActivityRepository(BaseRepository[BaseActivityModel]):
    def get_scoped_project_activities(
        self, project: Project, pagination: TimeBasedPagination, *, context: CardVisibilityContext,
        column: TColumnParam | None = None, card: TCardParam | None = None,
        assignee: TUserOrBotParam | None = None, only_count: bool = False,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int] | int:
        """Apply the same current card scope to pages and new-record counts."""
        if not context.active or not context.project_member:
            return 0 if only_count else ([], 0)
        scope = "card" if card is not None else "project_column" if column is not None else "project"
        ids = {"project": project.id}
        if column is not None:
            ids["project_column"] = InfraHelper.convert_id(column)
        if card is not None:
            ids["card"] = InfraHelper.convert_id(card)
        model, page_query, count_query, actor_filters = self.__create_refer_activity_queries(
            ProjectActivity, scope, assignee=assignee, **ids,
        )
        if model is ProjectActivity:
            page_query = SqlBuilder.select.table(model)
            count_query = SqlBuilder.select.count(model, model.id)
            actor_filters = {f"{key}_id": value for key, value in ids.items()}
        visible_card = select(Card.id).where(
            Card.id == ProjectActivity.card_id, Card.project_id == project.id,
            Card.deleted_at.is_(None), card_visibility_scope(context),
        ).exists()
        readable_activity = or_(ProjectActivity.card_id.is_(None), visible_card)
        if model is UserActivity:
            readable_activity = or_(
                and_(ProjectActivity.id.is_not(None), readable_activity),
                ProjectWikiActivity.id.is_not(None) if scope == "project" else False,
            )
        page_query = InfraHelper.where_recursive(page_query, model, **actor_filters).where(readable_activity)
        count_query = InfraHelper.where_recursive(count_query, model, **actor_filters).where(
            readable_activity, model.created_at > pagination.refer_time,
        )
        with DbSession.use(readonly=False) as db:
            count = db.exec(count_query).first() or 0
            if only_count:
                return count
            page_query = page_query.where(model.created_at <= pagination.refer_time).order_by(
                model.created_at.desc(), model.id.desc(),
            )
            page_query = InfraHelper.paginate(page_query, pagination.page, pagination.limit)
            return list(db.exec(page_query).all()), count

    def get_card_change_page(
        self, project: Project, limit: int, *, context: CardVisibilityContext,
        before_activity_uid: str | None = None,
    ) -> list[ProjectActivity]:
        """Filter primary card visibility before keyset pagination or its sentinel."""
        def scoped_query():
            return SqlBuilder.select.table(ProjectActivity).join(
                Card, Card.id == ProjectActivity.card_id,
            ).where(
                ProjectActivity.project_id == project.id, Card.project_id == project.id,
                Card.deleted_at.is_(None), card_visibility_scope(context),
            )

        with DbSession.use(readonly=False) as db:
            query = scoped_query()
            if before_activity_uid:
                cursor = db.exec(scoped_query().where(
                    ProjectActivity.id == InfraHelper.convert_id(before_activity_uid),
                )).first()
                if cursor is None:
                    return []
                query = query.where(or_(
                    ProjectActivity.created_at < cursor.created_at,
                    and_(ProjectActivity.created_at == cursor.created_at, ProjectActivity.id < cursor.id),
                ))
            return list(db.exec(query.order_by(
                ProjectActivity.created_at.desc(), ProjectActivity.id.desc(),
            ).limit(limit + 1)).all())

    def get_card_timeline_page(
        self, project: Project, card: Card, *, before: str | None = None, evidence_refs: list[str] | None = None,
        omit_refs: list[str] | None = None,
    ) -> list[tuple[str, int, datetime]]:
        """One shared order for comments and committed moves, with a scoped cursor and sentinel."""
        comments = select(
            literal("comment").label("kind"), CardComment.id.label("id"), CardComment.created_at.label("created_at"),
            CardComment.deleted_at.is_(None).label("active"),
        ).where(CardComment.card_id == card.id)
        moves = select(
            literal("activity").label("kind"), ProjectActivity.id.label("id"), ProjectActivity.created_at.label("created_at"),
            literal(True).label("active"),
        ).where(
            ProjectActivity.project_id == project.id, ProjectActivity.card_id == card.id,
            ProjectActivity.activity_type == ProjectActivityType.CardMoved,
        )
        events = union_all(comments, moves).subquery()
        with DbSession.use(readonly=True) as db:
            query = select(events.c.kind, events.c.id, events.c.created_at).where(events.c.active)
            if evidence_refs is not None:
                identities = []
                for ref in evidence_refs:
                    kind, separator, uid = ref.partition(":")
                    if not separator or kind not in {"comment", "activity"}:
                        return []
                    identities.append((kind, InfraHelper.convert_id(uid)))
                query = query.where(tuple_(events.c.kind, events.c.id).in_(identities))
            if omit_refs:
                omitted = [(kind, InfraHelper.convert_id(uid)) for kind, uid in (ref.split(":", 1) for ref in omit_refs)]
                query = query.where(tuple_(events.c.kind, events.c.id).not_in(omitted))
            if before:
                kind, separator, uid = before.partition(":")
                if not separator or kind not in {"comment", "activity"} or not uid or len(uid) > 32:
                    return []
                try:
                    cursor_id = InfraHelper.convert_id(uid)
                except (ValueError, TypeError):
                    return []
                cursor = db.exec(select(events.c.kind, events.c.id, events.c.created_at).where(
                    events.c.kind == kind, events.c.id == cursor_id,
                )).first()
                if cursor is None:
                    return []
                # Deleted comments remain valid position anchors, never returned as content.
                query = query.where(or_(
                    events.c.created_at < cursor[2],
                    and_(events.c.created_at == cursor[2], events.c.id < cursor[1]),
                    and_(events.c.created_at == cursor[2], events.c.id == cursor[1], events.c.kind < cursor[0]),
                ))
            return list(db.exec(query.order_by(
                events.c.created_at.desc(), events.c.id.desc(), events.c.kind.desc(),
            ).limit(21)).all())

    def get_card_column_history(
        self, project: Project, card: Card, *, before_activity_uid: str | None = None,
    ) -> list[ProjectActivity]:
        """Fetch one bounded page plus a sentinel; cursors stay inside this card."""
        def scoped_query():
            return SqlBuilder.select.table(ProjectActivity).where(
                ProjectActivity.project_id == project.id,
                ProjectActivity.card_id == card.id,
                ProjectActivity.activity_type.in_([ProjectActivityType.CardCreated, ProjectActivityType.CardMoved]),
            )

        with DbSession.use(readonly=True) as db:
            query = scoped_query()
            if before_activity_uid:
                cursor = db.exec(scoped_query().where(
                    ProjectActivity.id == InfraHelper.convert_id(before_activity_uid),
                )).first()
                if cursor is None:
                    return []
                query = query.where(or_(
                    ProjectActivity.created_at < cursor.created_at,
                    and_(ProjectActivity.created_at == cursor.created_at, ProjectActivity.id < cursor.id),
                ))
            return list(db.exec(query.order_by(
                ProjectActivity.created_at.desc(), ProjectActivity.id.desc(),
            ).limit(21)).all())

    def get_shared_user_activities(
        self,
        viewer: User,
        target: User,
        pagination: TimeBasedPagination,
        activity_uid: str | None = None,
        scope: Literal["project", "wiki"] | None = None,
        project_uid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        *, contexts: dict[int, CardVisibilityContext],
    ) -> list:
        """Authorize before paging; list queries never select editing history.

        Even administrators must belong to the same board as the target here.
        This is a collaboration history endpoint, not an administrator audit feed.
        """
        if not contexts:
            return []
        queries = []
        for kind, model in (("project", ProjectActivity), ("wiki", ProjectWikiActivity)):
            if scope and kind != scope:
                continue
            columns = [
                model.column("id"),
                model.column("created_at"),
                cast(model.column("activity_type"), String).label("activity_type"),
                literal(kind).label("scope"),
                Project.column("id").label("project_id"),
                Project.column("title").label("project_title"),
            ]
            if kind == "project":
                columns.extend([Card.column("id").label("resource_id"), Card.column("title").label("resource_title")])
            else:
                columns.extend(
                    [
                        ProjectWiki.column("id").label("resource_id"),
                        ProjectWiki.column("title").label("resource_title"),
                    ]
                )
            if activity_uid:
                columns.append(model.column("activity_history"))
            query = select(*columns).join(Project, Project.column("id") == model.column("project_id"))
            if kind == "project":
                query = query.outerjoin(Card, Card.column("id") == ProjectActivity.column("card_id"))
                query = query.where(project_activity_visibility_scope(contexts))
            else:
                query = query.join(
                    ProjectWiki, ProjectWiki.column("id") == ProjectWikiActivity.column("project_wiki_id")
                )
                query = query.where(ProjectWiki.column("deleted_at").is_(None), ProjectWikiActivity.project_id.in_(list(contexts)))
            query = query.where(
                model.column("user_id") == target.id,
                Project.column("deleted_at").is_(None),
                model.column("created_at") <= pagination.refer_time,
            )
            if project_uid:
                query = query.where(Project.column("id") == InfraHelper.convert_id(project_uid))
            if since:
                query = query.where(model.column("created_at") >= since)
            if until:
                query = query.where(model.column("created_at") < until)
            for person in (viewer, target):
                membership = (
                    select(ProjectAssignedUser.column("id"))
                    .where(
                        ProjectAssignedUser.column("project_id") == Project.column("id"),
                        ProjectAssignedUser.column("user_id") == person.id,
                    )
                    .exists()
                )
                query = query.where(membership)
                if not person.is_admin:
                    actions = literal(",") + cast(ProjectRole.column("actions"), String) + literal(",")
                    readable = (
                        select(ProjectRole.column("id"))
                        .where(
                            ProjectRole.column("project_id") == Project.column("id"),
                            ProjectRole.column("user_id") == person.id,
                            or_(actions.contains(",read,"), actions.contains(",*,")),
                        )
                        .exists()
                    )
                    query = query.where(or_(Project.column("owner_id") == person.id, readable))
                if kind == "wiki" and not person.is_admin:
                    assigned = (
                        select(ProjectWikiAssignedUser.column("id"))
                        .where(
                            ProjectWikiAssignedUser.column("project_wiki_id") == ProjectWiki.column("id"),
                            ProjectWikiAssignedUser.column("user_id") == person.id,
                        )
                        .exists()
                    )
                    query = query.where(
                        or_(
                            ProjectWiki.column("is_public").is_(True),
                            Project.column("owner_id") == person.id,
                            assigned,
                        )
                    )
            if activity_uid:
                query = query.where(model.column("id") == InfraHelper.convert_id(activity_uid))
            queries.append(query)
        combined = union_all(*queries).subquery()
        statement = select(combined).order_by(combined.c.created_at.desc(), combined.c.id.desc())
        if not activity_uid:
            statement = statement.offset((pagination.page - 1) * pagination.limit).limit(pagination.limit + 1)
        else:
            statement = statement.limit(1)
        with DbSession.use(readonly=False) as db:
            return list(db.exec(statement).all())

    @staticmethod
    @override
    def name() -> str:
        return "activity"

    def get_list_by_user(
        self, user: TUserParam, pagination: TimeBasedPagination, only_count: bool = False,
        *, contexts: dict[int, CardVisibilityContext],
    ) -> tuple[list[UserActivity], int] | int:
        """Revalidate referenced workspace/card access before page and count queries."""
        if not contexts:
            return 0 if only_count else ([], 0)
        user_id = InfraHelper.convert_id(user)
        wiki_assigned = select(ProjectWikiAssignedUser.id).where(
            ProjectWikiAssignedUser.project_wiki_id == ProjectWiki.id,
            ProjectWikiAssignedUser.user_id == user_id,
        ).exists()
        wiki_scope = and_(
            ProjectWikiActivity.project_id.in_(list(contexts)), ProjectWiki.deleted_at.is_(None),
            ProjectWiki.project_id == ProjectWikiActivity.project_id,
            or_(ProjectWiki.is_public.is_(True), Project.owner_id == user_id, wiki_assigned),
        )
        def scoped(query):
            return query.outerjoin(ProjectActivity, and_(
                UserActivity.refer_activity_table == ProjectActivity.__tablename__,
                UserActivity.refer_activity_id == ProjectActivity.id,
            )).outerjoin(ProjectWikiActivity, and_(
                UserActivity.refer_activity_table == ProjectWikiActivity.__tablename__,
                UserActivity.refer_activity_id == ProjectWikiActivity.id,
            )).outerjoin(ProjectWiki, ProjectWiki.id == ProjectWikiActivity.project_wiki_id).outerjoin(
                Project, Project.id == ProjectWikiActivity.project_id,
            ).where(
                UserActivity.user_id == user_id,
                or_(project_activity_visibility_scope(contexts), wiki_scope),
            )
        with DbSession.use(readonly=False) as db:
            count = db.exec(scoped(SqlBuilder.select.count(UserActivity, UserActivity.id)).where(
                UserActivity.created_at > pagination.refer_time,
            )).first() or 0
            if only_count:
                return count
            query = scoped(SqlBuilder.select.table(UserActivity)).where(
                UserActivity.created_at <= pagination.refer_time,
            ).order_by(UserActivity.created_at.desc(), UserActivity.id.desc())
            return list(db.exec(InfraHelper.paginate(query, pagination.page, pagination.limit)).all()), count

    @overload
    def get_list_by_project(
        self,
        project: TProjectParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: _TUserOrBotActivityParam,
    ) -> tuple[list[UserActivity], int]: ...
    @overload
    def get_list_by_project(
        self,
        project: TProjectParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int]: ...
    @overload
    def get_list_by_project(
        self,
        project: TProjectParam,
        pagination: TimeBasedPagination,
        only_count: Literal[True],
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> int: ...
    def get_list_by_project(
        self,
        project: TProjectParam,
        pagination: TimeBasedPagination,
        only_count: bool = False,
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int] | int:
        project_id = InfraHelper.convert_id(project)

        activity_class, list_query, outdated_query, where_clauses = self.__create_refer_activity_queries(
            ProjectActivity, "project", assignee=assignee, project=project_id
        )
        if not where_clauses:
            where_clauses = {"project_id": project_id}

        if only_count:
            return self.__count_activity_result(activity_class, pagination.refer_time, outdated_query, **where_clauses)

        result = self.__get_project_activity_result(
            activity_class, pagination, list_query, outdated_query, **where_clauses
        )
        return result

    @overload
    def get_list_by_column(
        self,
        project: TProjectParam,
        column: TColumnParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: _TUserOrBotActivityParam,
    ) -> tuple[list[UserActivity], int]: ...
    @overload
    def get_list_by_column(
        self,
        project: TProjectParam,
        column: TColumnParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int]: ...
    @overload
    def get_list_by_column(
        self,
        project: TProjectParam,
        column: TColumnParam,
        pagination: TimeBasedPagination,
        only_count: Literal[True],
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> int: ...
    def get_list_by_column(
        self,
        project: TProjectParam,
        column: TColumnParam,
        pagination: TimeBasedPagination,
        only_count: bool = False,
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int] | int:
        project_id = InfraHelper.convert_id(project)
        column_id = InfraHelper.convert_id(column)

        activity_class, list_query, outdated_query, where_clauses = self.__create_refer_activity_queries(
            ProjectActivity, "project", assignee=assignee, project=project_id, project_column=column_id
        )
        if not where_clauses:
            where_clauses = {"project_id": project_id, "column_id": column_id}

        if only_count:
            return self.__count_activity_result(activity_class, pagination.refer_time, outdated_query, **where_clauses)

        result = self.__get_project_activity_result(
            activity_class, pagination, list_query, outdated_query, **where_clauses
        )
        return result

    @overload
    def get_list_by_card(
        self,
        project: TProjectParam,
        card: TCardParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: _TUserOrBotActivityParam,
    ) -> tuple[list[UserActivity], int]: ...
    @overload
    def get_list_by_card(
        self,
        project: TProjectParam,
        card: TCardParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int]: ...
    @overload
    def get_list_by_card(
        self,
        project: TProjectParam,
        card: TCardParam,
        pagination: TimeBasedPagination,
        only_count: Literal[True],
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> int: ...
    def get_list_by_card(
        self,
        project: TProjectParam,
        card: TCardParam,
        pagination: TimeBasedPagination,
        only_count: bool = False,
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int] | int:
        project_id = InfraHelper.convert_id(project)
        card_id = InfraHelper.convert_id(card)

        activity_class, list_query, outdated_query, where_clauses = self.__create_refer_activity_queries(
            ProjectActivity, "project", assignee=assignee, project=project_id, card=card_id
        )
        if not where_clauses:
            where_clauses = {"project_id": project_id, "card_id": card_id}

        if only_count:
            return self.__count_activity_result(activity_class, pagination.refer_time, outdated_query, **where_clauses)

        result = self.__get_project_activity_result(
            activity_class, pagination, list_query, outdated_query, **where_clauses
        )
        return result

    @overload
    def get_list_by_wiki(
        self,
        project: TProjectParam,
        wiki: TWikiParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: _TUserOrBotActivityParam,
    ) -> tuple[list[UserActivity], int]: ...
    @overload
    def get_list_by_wiki(
        self,
        project: TProjectParam,
        wiki: TWikiParam,
        pagination: TimeBasedPagination,
        only_count: Literal[False] = False,
        *,
        assignee: None = None,
    ) -> tuple[list[ProjectWikiActivity] | list[UserActivity], int]: ...
    @overload
    def get_list_by_wiki(
        self,
        project: TProjectParam,
        wiki: TWikiParam,
        pagination: TimeBasedPagination,
        only_count: Literal[True],
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> int: ...
    def get_list_by_wiki(
        self,
        project: TProjectParam,
        wiki: TWikiParam,
        pagination: TimeBasedPagination,
        only_count: bool = False,
        *,
        assignee: TUserOrBotParam | None = None,
    ) -> tuple[list[ProjectWikiActivity] | list[UserActivity], int] | int:
        project_id = InfraHelper.convert_id(project)
        wiki_id = InfraHelper.convert_id(wiki)

        activity_class, list_query, outdated_query, where_clauses = self.__create_refer_activity_queries(
            ProjectWikiActivity, "project", assignee=assignee, project=project_id, project_wiki=wiki_id
        )
        if not where_clauses:
            where_clauses = {"project_id": project_id, "project_wiki_id": wiki_id}

        if only_count:
            return self.__count_activity_result(activity_class, pagination.refer_time, outdated_query, **where_clauses)

        result = self.__get_wiki_activity_result(
            activity_class, pagination, list_query, outdated_query, **where_clauses
        )
        return result

    def get_user_or_bot(self, user_or_bot_param: TUserOrBotParam) -> User | Bot | None:
        if isinstance(user_or_bot_param, (User, Bot)):
            return user_or_bot_param

        user_or_bot_id = InfraHelper.convert_id(user_or_bot_param)
        user = InfraHelper.get_by(User, "id", user_or_bot_id)
        if user:
            return user

        return InfraHelper.get_by(Bot, "id", user_or_bot_id)

    def __get_list(
        self,
        activity_class: type[_TActivityModel],
        pagination: TimeBasedPagination,
        list_query: SelectOfScalar[_TActivityModel] | None = None,
        outdated_query: SelectOfScalar[int] | None = None,
        **where_clauses: SnowflakeID,
    ) -> tuple[list[_TActivityModel], int]:
        if list_query is None:
            list_query = SqlBuilder.select.table(activity_class)
        list_query = self.__make_query(list_query, activity_class, **where_clauses)
        list_query = list_query.where(activity_class.column("created_at") <= pagination.refer_time)
        list_query = InfraHelper.paginate(list_query, pagination.page, pagination.limit)
        records = []
        # Wiki history can contain editor snapshots redacted on the primary.
        with DbSession.use(readonly=activity_class is not ProjectWikiActivity) as db:
            result = db.exec(list_query)
            records = result.all()

        count_new_records = self.__count_new_records(
            activity_class, pagination.refer_time, outdated_query, **where_clauses
        )

        return records, count_new_records

    def __count_new_records(
        self,
        activity_class: type[_TActivityModel],
        refer_time: SafeDateTime,
        outdated_query: SelectOfScalar[int] | None = None,
        **where_clauses: SnowflakeID,
    ) -> int:
        if outdated_query is None:
            outdated_query = SqlBuilder.select.count(activity_class, activity_class.column("id", SnowflakeID))
        outdated_query = self.__make_query(outdated_query, activity_class, **where_clauses)
        outdated_query = outdated_query.where(activity_class.column("created_at", SafeDateTime) > refer_time)
        record = 0
        with DbSession.use(readonly=True) as db:
            result = db.exec(outdated_query)
            record = result.first() or 0
        return record

    def __make_query(
        self,
        query: SelectOfScalar[_TSelectParam],
        activity_class: type[_TActivityModel],
        **where_clauses: SnowflakeID,
    ) -> SelectOfScalar[_TSelectParam]:
        query = query.order_by(activity_class.column("created_at", SafeDateTime).desc()).group_by(
            activity_class.column("id", SnowflakeID), activity_class.column("created_at", SafeDateTime)
        )
        query = InfraHelper.where_recursive(query, activity_class, **where_clauses)
        return query

    def __get_project_activity_result(
        self,
        activity_class: type[ProjectActivity] | type[UserActivity],
        pagination: TimeBasedPagination,
        list_query: SelectOfScalar[UserActivity] | None,
        outdated_query: SelectOfScalar[int] | None,
        **where_clauses: SnowflakeID,
    ) -> tuple[list[ProjectActivity] | list[UserActivity], int]:
        if activity_class is UserActivity:
            return self.__get_list(UserActivity, pagination, list_query, outdated_query, **where_clauses)

        return self.__get_list(ProjectActivity, pagination, None, None, **where_clauses)

    def __get_wiki_activity_result(
        self,
        activity_class: type[ProjectWikiActivity] | type[UserActivity],
        pagination: TimeBasedPagination,
        list_query: SelectOfScalar[UserActivity] | None,
        outdated_query: SelectOfScalar[int] | None,
        **where_clauses: SnowflakeID,
    ) -> tuple[list[ProjectWikiActivity] | list[UserActivity], int]:
        if activity_class is UserActivity:
            return self.__get_list(UserActivity, pagination, list_query, outdated_query, **where_clauses)

        return self.__get_list(ProjectWikiActivity, pagination, None, None, **where_clauses)

    def __count_activity_result(
        self,
        activity_class: type[_TScopedActivityModel] | type[UserActivity],
        refer_time: SafeDateTime,
        outdated_query: SelectOfScalar[int] | None,
        **where_clauses: SnowflakeID,
    ) -> int:
        if activity_class is UserActivity:
            return self.__count_new_records(UserActivity, refer_time, outdated_query, **where_clauses)

        return self.__count_new_records(activity_class, refer_time, None, **where_clauses)

    def __create_refer_activity_queries(
        self,
        activity_class: type[_TScopedActivityModel],
        scope: _TActivityScope,
        *,
        assignee: TUserOrBotParam | None = None,
        **kwargs: SnowflakeID,
    ) -> tuple[
        type[_TScopedActivityModel] | type[UserActivity],
        SelectOfScalar[UserActivity] | None,
        SelectOfScalar[int] | None,
        dict[str, SnowflakeID],
    ]:
        if not assignee:
            return activity_class, None, None, {}

        assignee = self.get_user_or_bot(assignee)
        if not assignee:
            return activity_class, None, None, {}

        list_query = self.__refer(SqlBuilder.select.table(UserActivity), scope, **kwargs)
        outdated_query = self.__refer(
            SqlBuilder.select.count(UserActivity, UserActivity.column("id", SnowflakeID)), scope, **kwargs
        )
        where_clauses: dict[str, SnowflakeID] = {}

        if isinstance(assignee, User):
            where_clauses["user_id"] = assignee.id
        else:
            where_clauses["bot_id"] = assignee.id

        return UserActivity, list_query, outdated_query, where_clauses

    @overload
    def __refer(
        self, query: SelectOfScalar[_TActivityModel], scope: _TActivityScope, **kwargs: SnowflakeID
    ) -> SelectOfScalar[_TActivityModel]: ...
    @overload
    def __refer(
        self, query: SelectOfScalar[int], scope: _TActivityScope, **kwargs: SnowflakeID
    ) -> SelectOfScalar[int]: ...
    def __refer(
        self,
        query: SelectOfScalar[_TActivityModel] | SelectOfScalar[int],
        scope: _TActivityScope,
        **kwargs: SnowflakeID,
    ) -> SelectOfScalar[_TActivityModel] | SelectOfScalar[int]:
        tables = []
        if scope in {"project", "project_wiki"}:
            query = query.outerjoin(
                ProjectActivity,
                (UserActivity.column("refer_activity_table") == ProjectActivity.__tablename__)
                & (ProjectActivity.column("id") == UserActivity.column("refer_activity_id")),
            ).outerjoin(
                ProjectWikiActivity,
                (UserActivity.column("refer_activity_table") == ProjectWikiActivity.__tablename__)
                & (ProjectWikiActivity.column("id") == UserActivity.column("refer_activity_id")),
            )
            tables = [ProjectActivity, ProjectWikiActivity]
        elif scope in {"project_column", "card"}:
            query = query.outerjoin(
                ProjectActivity,
                (UserActivity.column("refer_activity_table") == ProjectActivity.__tablename__)
                & (ProjectActivity.column("id") == UserActivity.column("refer_activity_id")),
            )
            tables = [ProjectActivity]

        for key, value in kwargs.items():
            value = InfraHelper.convert_id(value)
            key = f"{key}_id"

            or_clauses = None
            for table in tables:
                if key not in table.model_fields:
                    continue

                if or_clauses is None:
                    or_clauses = table.column(key) == value
                else:
                    or_clauses |= table.column(key) == value

            if or_clauses is not None:
                query = query.where(or_clauses)

        return query
