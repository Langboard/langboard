from sqlalchemy import update
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime
from ....core.types.ParamTypes import TCardParam, TCommentParam
from ....domain.models import Bot, CardComment, User
from ....helpers import InfraHelper


class CardCommentRepository(BaseRepository[CardComment]):
    @staticmethod
    def model_cls():
        return CardComment

    @staticmethod
    def name() -> str:
        return "card_comment"

    def update_if_current(self, comment: CardComment, expected_revision: str) -> bool:
        """Compare the saved revision while holding the primary row lock."""
        submitted = comment.content
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(CardComment).where(
                CardComment.id == comment.id, CardComment.card_id == comment.card_id,
                CardComment.deleted_at.is_(None),
            ).with_for_update().execution_options(populate_existing=True)).first()
            if current is None or current.content_revision() != expected_revision:
                return False
            updated_at = SafeDateTime.now()
            changed = db.exec(update(CardComment.__table__).where(CardComment.id == comment.id).values(
                content=submitted, updated_at=updated_at,
            ))
            if changed != 1:
                return False
        comment.content = submitted
        comment.updated_at = updated_at
        comment.clear_changes()
        return True

    def get_list_by_card(self, card: TCardParam, *, comment_ids: list[int] | None = None):
        card_id = InfraHelper.convert_id(card)
        comments = []
        with DbSession.use(readonly=True) as db:
            query = self.__get_board_comment_api_query(card_id)
            if comment_ids is not None:
                query = query.where(CardComment.column("id").in_(comment_ids))
            result = db.exec(query)
            comments = result.all()

        return comments

    def get_page_by_card(
        self,
        card: TCardParam,
        limit: int,
        before_created_at: SafeDateTime | None = None,
        before_comment: TCommentParam | None = None,
    ):
        """Return one newest-first comment page plus one look-ahead row."""

        card_id = InfraHelper.convert_id(card)
        query = self.__get_board_comment_api_query(card_id).where(CardComment.column("deleted_at") == None)  # noqa
        if before_created_at is not None and before_comment is not None:
            comment_id = InfraHelper.convert_id(before_comment)
            query = query.where(
                (CardComment.column("created_at") < before_created_at)
                | ((CardComment.column("created_at") == before_created_at) & (CardComment.column("id") < comment_id))
            )
        with DbSession.use(readonly=True) as db:
            return db.exec(query.limit(limit + 1)).all()

    def get_one(self, card: TCardParam, comment: TCommentParam):
        card_id = InfraHelper.convert_id(card)
        comment_id = InfraHelper.convert_id(comment)
        record = None
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                self.__get_board_comment_api_query(card_id).where(CardComment.column("id") == comment_id).limit(1)
            )
            record = result.first()
        return record

    def count_by_card(self, card: TCardParam) -> int:
        card_id = InfraHelper.convert_id(card)
        with DbSession.use(readonly=True) as db:
            return (
                db.exec(
                    SqlBuilder.select.count(CardComment, CardComment.column("id")).where(
                        (CardComment.column("card_id") == card_id) & (CardComment.column("deleted_at") == None)  # noqa
                    )
                ).first()
                or 0
            )

    def __get_board_comment_api_query(self, card_id: int):
        return (
            SqlBuilder.select.tables(CardComment, User, Bot, with_deleted=True)
            .outerjoin(User, CardComment.column("user_id") == User.column("id"))
            .outerjoin(Bot, CardComment.column("bot_id") == Bot.column("id"))
            .where(CardComment.column("card_id") == card_id)
            .order_by(
                CardComment.column("created_at").desc(),
                CardComment.column("id").desc(),
            )
        )
