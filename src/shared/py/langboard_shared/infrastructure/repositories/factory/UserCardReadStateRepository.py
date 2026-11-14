from sqlalchemy import case, func, or_, select
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TCardParam, TUserParam
from ....domain.models import (
    Card,
    CardChangeReceipt,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    User,
    UserCardReadState,
)
from ....domain.services.CardVisibilityPolicy import CardVisibilityContext, card_visibility_scope
from ....helpers import InfraHelper


class UserCardReadStateRepository(BaseRepository[UserCardReadState]):
    @staticmethod
    def model_cls():
        return UserCardReadState

    @staticmethod
    def name() -> str:
        return "user_card_read_state"

    def get_seen_seq_map(self, user_id: SnowflakeID, card_ids: list[SnowflakeID]) -> dict[int, int]:
        """Return card_id -> seen_change_seq for one user's cards."""

        if not card_ids:
            return {}
        states = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(UserCardReadState)
                .where((UserCardReadState.column("user_id") == user_id) & UserCardReadState.column("card_id").in_(card_ids))
            )
            states = result.all()
        return {state.card_id: state.seen_change_seq for state in states}

    def get_change_state_map(
        self, user_id: SnowflakeID, card_ids: list[SnowflakeID], *, context: CardVisibilityContext
    ) -> dict[int, dict]:
        """Bounded primary batches; indexed native receipts, never activity scans.

        The board caller resolves context in the same repeatable primary snapshot.
        Membership, card scope, baseline, cursor, latest and count are also joined
        here so a revoked or moved row cannot be projected from caller objects.
        """
        if not card_ids:
            return {}
        seen = func.coalesce(UserCardReadState.seen_change_seq, 0)
        baseline = func.coalesce(ProjectAssignedUser.card_unread_baseline_seq, 0)
        cursor = case((seen > baseline, seen), else_=baseline)
        columns = (
            Card.id, Card.last_change_seq, Card.last_change_target_type,
            Card.last_change_target_id, Card.last_change_at, Card.change_count_since_seq,
            Card.source_type, Card.source_uid, seen, baseline,
        )
        card_ids = list(dict.fromkeys(card_ids))
        with DbSession.use(readonly=False) as db:
            query = (
                select(*columns, func.count(CardChangeReceipt.id), func.max(CardChangeReceipt.change_seq))
                .select_from(Card)
                .join(Project, Project.id == Card.project_id)
                .join(User, User.id == user_id)
                .outerjoin(ProjectAssignedUser, (ProjectAssignedUser.project_id == Card.project_id)
                           & (ProjectAssignedUser.user_id == user_id))
                .outerjoin(UserCardReadState, (UserCardReadState.card_id == Card.id)
                           & (UserCardReadState.user_id == user_id))
                .outerjoin(CardChangeReceipt, (CardChangeReceipt.card_id == Card.id)
                           & (CardChangeReceipt.change_seq > cursor)
                           & (CardChangeReceipt.change_seq <= Card.last_change_seq))
                .where(Card.deleted_at.is_(None), Project.deleted_at.is_(None),
                       User.deleted_at.is_(None), User.activated_at.is_not(None),
                       or_(Project.owner_id == user_id, ProjectAssignedUser.id.is_not(None)),
                       card_visibility_scope(context))
                .group_by(*columns)
            )
            rows = []
            for offset in range(0, len(card_ids), 200):
                rows.extend(db.exec(query.where(Card.id.in_(card_ids[offset:offset + 200]))).all())
        result = {}
        for card_id, latest, kind, target, at, boundary, source_type, source_uid, seen_seq, baseline_seq, count, newest in rows:
            cursor_seq = max(seen_seq, baseline_seq)
            manual = seen_seq < 0
            mirror = source_type is not None and source_uid is not None
            unread = not mirror and (manual or latest > cursor_seq)
            if mirror:
                status, count = "not_applicable", None
            elif manual:
                status, count = "manual_unread", None
            elif not unread:
                status, count = "exact", 0
            elif cursor_seq < boundary or newest != latest:
                status, count = "unknown_history", None
            else:
                status = "exact"
            result[card_id] = {
                "has_unread_change": unread,
                "unread_change_count": count,
                "unread_change_count_status": status,
                "latest_change": {"seq": latest, "target_type": kind,
                                  "target_uid": target.to_short_code() if target else None,
                                  "at": at.isoformat() if at else None},
            }
        return result

    def get_unread_summary_page(
        self, user_id: SnowflakeID, project_id: SnowflakeID, *, context: CardVisibilityContext,
        limit: int, archive_visible_since: SafeDateTime, before: tuple[int, int] | None = None,
    ) -> list:
        """Select summary scalars; current authority and unread filtering precede LIMIT."""
        seen = func.coalesce(UserCardReadState.seen_change_seq, 0)
        baseline = func.coalesce(ProjectAssignedUser.card_unread_baseline_seq, 0)
        cursor = case((seen > baseline, seen), else_=baseline)
        query = (
            select(Card.id, Card.title, Card.last_change_seq).select_from(Card)
            .join(Project, Project.id == Card.project_id)
            .join(ProjectColumn, (ProjectColumn.id == Card.project_column_id)
                  & (ProjectColumn.project_id == Card.project_id))
            .join(User, User.id == user_id)
            .outerjoin(ProjectAssignedUser, (ProjectAssignedUser.project_id == Card.project_id)
                       & (ProjectAssignedUser.user_id == user_id))
            .outerjoin(UserCardReadState, (UserCardReadState.card_id == Card.id)
                       & (UserCardReadState.user_id == user_id))
            .where(Card.project_id == project_id, Card.deleted_at.is_(None),
                   Project.deleted_at.is_(None), ProjectColumn.deleted_at.is_(None),
                   User.deleted_at.is_(None), User.activated_at.is_not(None),
                   or_(Project.owner_id == user_id, ProjectAssignedUser.id.is_not(None)),
                   card_visibility_scope(context),
                   or_(Card.source_type.is_(None), Card.source_type.not_in(("project_wiki", "project_card"))),
                   or_(Card.source_type.is_(None), Card.source_uid.is_(None)),
                   or_(Card.archived_at.is_(None), Card.archived_at >= archive_visible_since),
                   or_(seen < 0, Card.last_change_seq > cursor))
        )
        if before is not None:
            seq, card_id = before
            query = query.where(or_(Card.last_change_seq < seq,
                                   (Card.last_change_seq == seq) & (Card.id < card_id)))
        query = query.order_by(Card.last_change_seq.desc(), Card.id.desc()).limit(limit + 1)
        with DbSession.use(readonly=False) as db:
            return list(db.exec(query).all())

    def get_readers(self, card: Card) -> list[dict]:
        """Expose existing read receipts only for current board members."""
        with DbSession.use(readonly=False) as db:
            rows = db.exec(
                SqlBuilder.select.tables(UserCardReadState, User)
                .join(User, User.column("id") == UserCardReadState.column("user_id"))
                .join(ProjectAssignedUser, ProjectAssignedUser.column("user_id") == User.column("id"))
                .where(UserCardReadState.column("card_id") == card.id)
                .where(UserCardReadState.column("seen_change_seq") >= 0)
                .where(ProjectAssignedUser.column("project_id") == card.project_id)
                .where(User.activated_at.is_not(None))
                .where(User.id == card.owner_user_id if card.visibility == "PRIVATE" else True)
            ).all()
        return [{"user_uid": user.get_uid(), "seen_at": state.seen_at.isoformat()} for state, user in rows]

    def set_read_state(self, user: TUserParam, card: Card, seen: bool) -> UserCardReadState:
        """Serialize receipt creation and unread changes on the existing card row.

        A negative cursor is an explicit unread override, including old cards
        below the board baseline. No separate acknowledgement state is created.
        """
        user_id = InfraHelper.convert_id(user)
        with DbSession.atomic() as db:
            current = db.exec(
                SqlBuilder.select.table(Card).where(Card.column("id") == card.id).with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if current is None:
                raise ValueError("Card not found")
            state = db.exec(
                SqlBuilder.select.table(UserCardReadState)
                .where(UserCardReadState.column("user_id") == user_id)
                .where(UserCardReadState.column("card_id") == card.id)
            ).first()
            cursor = current.last_change_seq if seen else -1
            if state is None:
                state = UserCardReadState(user_id=user_id, card_id=card.id, seen_change_seq=cursor, seen_at=SafeDateTime.now())
                db.insert(state)
            elif not seen or cursor > state.seen_change_seq:
                state.seen_change_seq = cursor
                state.seen_at = SafeDateTime.now()
                db.update(state)
        return state

    def upsert_seen(self, user: TUserParam, card: TCardParam, seen_change_seq: int) -> UserCardReadState:
        resolved = InfraHelper.get_by_id_like(Card, card)
        if resolved is None:
            raise ValueError("Card not found")
        return self.set_read_state(user, resolved, True)
