from hashlib import sha256
from json import dumps, loads
from typing import Any
from uuid import UUID
from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from ....core.db import DbSession, EditorContentModel, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.exceptions.CardCommentConflict import CardCommentConflict
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime
from ....core.types.ParamTypes import TCardParam, TCommentParam, TProjectParam, TUserOrBot
from ....helpers import InfraHelper
from ....publishers import CardCommentPublisher
from ....tasks.activities import CardCommentActivityTask
from ....tasks.bots import CardCommentBotTask
from ...models import (
    Bot,
    Card,
    CardComment,
    CardCommentReaction,
    CardMetadata,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from ...models.bases import REACTION_TYPES
from ...models.CardComment import CardCommentAnchor, CardCommentImageAnchorModel
from ...models.ProjectRole import ProjectRoleAction
from ..CardAppMutation import guard_card_app_mutation
from ..CardChangeTransaction import card_change_transaction, dispatch_after_commit
from .CardService import CardService
from .NotificationService import NotificationService
from .ReactionService import ReactionService


class CardCommentService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "card_comment"

    def get_by_id_like(self, comment: TCommentParam | None) -> CardComment | None:
        comment = InfraHelper.get_by_id_like(CardComment, comment)
        return comment

    @staticmethod
    def can_mutate(user_or_bot: TUserOrBot, comment: CardComment) -> bool:
        """Return whether the actor owns the comment or is an administrator."""

        if isinstance(user_or_bot, User):
            return comment.user_id == user_or_bot.id or user_or_bot.is_admin
        return isinstance(user_or_bot, Bot) and comment.bot_id == user_or_bot.id

    def get_api_list_by_card(
        self, card: TCardParam | None, *, comment_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return []
        raw_comments = self.repo.card_comment.get_list_by_card(card, comment_ids=comment_ids)

        reaction_service = self._get_service(ReactionService)
        reactions = reaction_service.get_api_map(CardCommentReaction, [comment.id for comment, _, _ in raw_comments])

        comments = []
        for raw_comment in raw_comments:
            api_comment = self.convert_to_api_response(raw_comment, reactions.get(raw_comment[0].id))
            if api_comment:
                comments.append(api_comment)

        return comments

    def get_api_page_by_card(
        self,
        card: TCardParam | None,
        limit: int,
        before_created_at: SafeDateTime | None = None,
        before_comment: TCommentParam | None = None,
    ) -> tuple[list[dict[str, Any]], int, tuple[str, str] | None]:
        """Return a bounded newest-first page, total count, and next cursor fields."""

        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return [], 0, None
        raw_comments = self.repo.card_comment.get_page_by_card(card, limit, before_created_at, before_comment)
        has_more = len(raw_comments) > limit
        page = raw_comments[:limit]

        reaction_service = self._get_service(ReactionService)
        reactions = reaction_service.get_api_map(CardCommentReaction, [comment.id for comment, _, _ in page])
        comments = [
            api_comment
            for comment, user, bot in page
            if (api_comment := self.convert_to_api_response((comment, user, bot), reactions.get(comment.id)))
            is not None
        ]
        next_fields = None
        if has_more and page:
            last_comment = page[-1][0]
            next_fields = (last_comment.created_at.isoformat(), last_comment.get_uid())
        return comments, self.repo.card_comment.count_by_card(card), next_fields

    def get_as_api(self, card: TCardParam | None, comment: TCommentParam | None) -> dict[str, Any] | None:
        if not comment:
            return None
        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return None
        comment_id = InfraHelper.convert_id(comment)
        raw_comment = self.repo.card_comment.get_one(card, comment_id)
        if not raw_comment:
            return None

        reaction_service = self._get_service(ReactionService)
        reactions = reaction_service.get_api_map(CardCommentReaction, [comment_id])

        return self.convert_to_api_response(raw_comment, reactions.get(raw_comment[0].id))

    def convert_to_api_response(
        self, result: tuple[CardComment, User, Bot], reaction: dict[str, list[str]] | None = None
    ) -> dict[str, Any] | None:
        comment, user, bot = result
        if comment.deleted_at is not None:
            return None
        api_comment = comment.api_response()
        if user:
            api_comment["user"] = user.api_response()
        else:
            api_comment["bot"] = bot.api_response()
        api_comment["reactions"] = {kind: uids for kind, uids in (reaction or {}).items() if kind in REACTION_TYPES}
        return api_comment

    @guard_card_app_mutation
    @card_change_transaction
    def create(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        content: EditorContentModel | dict[str, Any],
        anchor: CardCommentAnchor | dict[str, Any] | None = None,
        *,
        dispatch_effects: bool = True,
        request_uid: str | None = None,
        channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> CardComment | dict[str, Any] | None:
        anchor = TypeAdapter(CardCommentAnchor).validate_python(anchor) if anchor is not None else None
        if isinstance(anchor, CardCommentImageAnchorModel):
            from ..CardCommentImageAnchors import create_image_comment

            return create_image_comment(
                self,
                user_or_bot,
                project,
                card,
                content,
                anchor,
                request_uid=request_uid,
                dispatch_effects=dispatch_effects,
                channel=channel,
            )
        if request_uid is not None:
            return self._create_requested_comment(
                user_or_bot,
                project,
                card,
                content,
                anchor,
                request_uid=request_uid,
                dispatch_effects=dispatch_effects,
                channel=channel,
            )
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if card.is_linked_resource:
            return None

        if isinstance(content, dict):
            content = EditorContentModel(**content)

        comment_params = {
            "card_id": card.id,
            "content": content,
            "anchor": anchor.model_dump() if anchor else None,
        }

        if isinstance(user_or_bot, User):
            comment_params["user_id"] = user_or_bot.id
        else:
            comment_params["bot_id"] = user_or_bot.id

        comment = CardComment(**comment_params)
        self.repo.card_comment.insert(comment)

        card_service = self._get_service(CardService)
        card_service.mark_card_changed(card, CardService.UNREAD_TARGET_COMMENT, comment.id)

        if dispatch_effects:
            self.dispatch_created(user_or_bot, project, card, comment)

        return comment

    def _create_requested_comment(
        self,
        actor,
        project,
        card,
        content,
        anchor,
        *,
        request_uid,
        dispatch_effects,
        channel,
    ):
        """Persist a protected native metadata receipt in the comment transaction.

        UUIDs identify one ordinary create globally. Only authenticated users
        have the current native authority checks required by this contract.
        """
        if not isinstance(actor, User):
            raise ValueError("Ordinary comment request receipts require a user actor")
        try:
            request_uid = str(UUID(request_uid))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Ordinary comments require a valid request UUID") from None
        content = EditorContentModel(**content) if isinstance(content, dict) else content
        project_id, card_id = InfraHelper.convert_id(project), InfraHelper.convert_id(card)
        key = "internal.comment_request." + request_uid
        fingerprint = sha256(
            dumps(
                [
                    str(actor.id),
                    str(project_id),
                    str(card_id),
                    content.model_dump(mode="json"),
                    anchor.model_dump() if anchor else None,
                    dispatch_effects,
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        from ..AppGovernance import lock_policy_mutation
        from ..CardCrossBoardOperations import _grant

        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                # Reserve the writer before decision reads, including absent receipts.
                db.exec(update(Card).where(Card.id == card_id).values(id=Card.id))
            elif DbEngine.get_main_engine().dialect.name == "postgresql":
                # A UUID can be reused against another card. Serialize its absent
                # metadata row before owner locks, using a transaction scoped key.
                lock_key = int.from_bytes(sha256(key.encode()).digest()[:8], "big", signed=True)
                db.exec(select(func.pg_advisory_xact_lock(lock_key)))
            else:
                raise ValueError("Ordinary comment request receipts require SQLite or PostgreSQL")
            actor = db.exec(
                SqlBuilder.select.table(User)
                .where(User.id == actor.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            project = db.exec(
                SqlBuilder.select.table(Project)
                .where(Project.id == project_id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
            card = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.id == card_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if (
                actor is None
                or project is None
                or card is None
                or card.project_id != project.id
                or card.is_linked_resource
            ):
                return None
            for model in (ProjectAssignedUser, ProjectRole):
                db.exec(
                    SqlBuilder.select.table(model)
                    .where(
                        model.project_id == project.id,
                        model.user_id == actor.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
            cards = self._get_service(CardService)
            if not _grant(db, actor, project, ProjectRoleAction.Read):
                return None
            if cards.resolve_readable_card(project, card, actor, channel) is None:
                return None
            receipt = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.key == key)).first()
            if receipt is not None:
                stored = loads(receipt.value)
                if stored["fingerprint"] != fingerprint:
                    raise ValueError("Ordinary comment request UUID already belongs to another payload")
                comment = db.exec(
                    SqlBuilder.select.table(CardComment, with_deleted=True).where(
                        CardComment.id == InfraHelper.convert_id(stored["comment_uid"]),
                        CardComment.card_id == card.id,
                    )
                ).first()
                if comment is None or comment.deleted_at is not None:
                    return None
                return {"comment_uid": comment.get_uid(), "replayed": True}
            comment = self.create(
                actor, project, card, content, anchor, dispatch_effects=dispatch_effects, channel=channel
            )
            if comment is None:
                return None
            db.insert(
                CardMetadata(
                    card_id=card.id,
                    key=key,
                    value=dumps(
                        {
                            "fingerprint": fingerprint,
                            "comment_uid": comment.get_uid(),
                        },
                        sort_keys=True,
                    ),
                )
            )
            return {"comment_uid": comment.get_uid(), "replayed": False}

    def dispatch_created(
        self,
        user_or_bot: TUserOrBot,
        project: Project,
        card: Card,
        comment: CardComment,
        *,
        include_notifications: bool = True,
        include_bot: bool = True,
    ) -> None:
        dispatch_after_commit(CardCommentPublisher.created, user_or_bot, project, card, comment)
        if include_notifications:
            notification_service = self._get_service(NotificationService)
            notification_service.notify_mentioned_in_comment(user_or_bot, project, card, comment)
        dispatch_after_commit(CardCommentActivityTask.card_comment_added, user_or_bot, project, card, comment)
        if include_bot:
            dispatch_after_commit(CardCommentBotTask.card_comment_added, user_or_bot, project, card, comment)

    @guard_card_app_mutation
    @card_change_transaction
    def update(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        comment: TCommentParam | None,
        content: EditorContentModel | dict[str, Any],
        *,
        expected_revision: str | None = None,
    ) -> CardComment | None:
        params = InfraHelper.get_records_with_foreign_by_params(
            (Project, project), (Card, card), (CardComment, comment)
        )
        if not params:
            return None
        project, card, comment = params
        if not self.can_mutate(user_or_bot, comment):
            return None

        if isinstance(content, dict):
            content = EditorContentModel(**content)

        old_content = comment.content
        comment.content = content
        if expected_revision is None:
            self.repo.card_comment.update(comment)
        elif not self.repo.card_comment.update_if_current(comment, expected_revision):
            raise CardCommentConflict("Comment changed after editing began")

        card_service = self._get_service(CardService)
        card_service.mark_card_changed(card, CardService.UNREAD_TARGET_COMMENT, comment.id)

        dispatch_after_commit(CardCommentPublisher.updated, project, card, comment)

        notification_service = self._get_service(NotificationService)
        notification_service.notify_mentioned_in_comment(user_or_bot, project, card, comment)

        dispatch_after_commit(
            CardCommentActivityTask.card_comment_updated, user_or_bot, project, card, old_content, comment,
        )
        dispatch_after_commit(CardCommentBotTask.card_comment_updated, user_or_bot, project, card, comment)

        return comment

    @guard_card_app_mutation
    @card_change_transaction
    def delete(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        comment: TCommentParam | None,
    ) -> CardComment | None:
        params = InfraHelper.get_records_with_foreign_by_params(
            (Project, project), (Card, card), (CardComment, comment)
        )
        if not params:
            return None
        project, card, comment = params
        if not self.can_mutate(user_or_bot, comment):
            return None

        self.repo.card_comment.delete(comment)

        card_service = self._get_service(CardService)
        card_service.mark_card_changed(card, CardService.UNREAD_TARGET_COMMENT, comment.id)

        dispatch_after_commit(CardCommentPublisher.deleted, project, card, comment)
        dispatch_after_commit(CardCommentActivityTask.card_comment_deleted, user_or_bot, project, card, comment)
        dispatch_after_commit(CardCommentBotTask.card_comment_deleted, user_or_bot, project, card, comment)

        return comment

    @guard_card_app_mutation
    def toggle_reaction(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        comment: TCommentParam | None,
        reaction: str,
    ) -> bool | None:
        if reaction not in REACTION_TYPES:
            raise ValueError("Unsupported comment reaction")
        from ..AppCardInteractions import accept_current_comment_reaction, pending_comment_interactions
        from ..AppGovernance import lock_policy_mutation

        with DbSession.atomic() as db:
            params = InfraHelper.get_records_with_foreign_by_params(
                (Project, project), (Card, card), (CardComment, comment)
            )
            if not params:
                return None
            project, card, comment = params
            reaction_service = self._get_service(ReactionService)
            if isinstance(user_or_bot, User) and pending_comment_interactions(db, project.id, card.id, comment.id):
                lock_policy_mutation(db)
            is_reacted = reaction_service.toggle(user_or_bot, CardCommentReaction, comment.id, reaction)
            if is_reacted:
                from ..DomainService import DomainService

                accept_current_comment_reaction(DomainService(), user_or_bot, project.id, card.id, comment.id, reaction)

            def publish_reaction():
                CardCommentPublisher.reacted(user_or_bot, project, card, comment, reaction, is_reacted)

                if is_reacted and comment.user_id:
                    notification_service = self._get_service(NotificationService)
                    notification_service.notify_reacted_to_comment(user_or_bot, project, card, comment, reaction)

                if is_reacted:
                    CardCommentActivityTask.card_comment_reacted(user_or_bot, project, card, comment, reaction)
                    CardCommentBotTask.card_comment_reacted(user_or_bot, project, card, comment, reaction)
                else:
                    CardCommentActivityTask.card_comment_unreacted(user_or_bot, project, card, comment, reaction)
                    CardCommentBotTask.card_comment_unreacted(user_or_bot, project, card, comment, reaction)

            db.after_commit(publish_reaction)

        return is_reacted
