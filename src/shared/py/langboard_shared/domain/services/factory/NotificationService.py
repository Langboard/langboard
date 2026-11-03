from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Literal, NotRequired, TypedDict, TypeGuard, TypeVar, cast, get_args, overload
from urllib.parse import urlparse
from sqlalchemy import BigInteger, func, literal_column, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from ....core.db import BaseDbModel, DbSession, EditorContentModel, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.logger import Logger
from ....core.resources.locales.EmailTemplateNames import TEmailTemplateName
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TNotificationParam, TUserOrBot, TUserParam
from ....core.utils.EditorContentParser import change_date_element, find_mentioned
from ....core.utils.String import concat
from ....Env import UI_QUERY_NAMES, Env
from ....helpers import InfraHelper
from ....publishers.UserPublisher import UserPublisher
from ....tasks.bots import BotDefaultTask
from ....tasks.notifications.NotificationWorkEventTask import publish_pending_work_events
from ...models import (
    Bot,
    Card,
    CardComment,
    Checkitem,
    Checklist,
    NotificationEmailDelivery,
    Project,
    ProjectColumn,
    ProjectInvitation,
    ProjectWiki,
    ProjectWikiAssignedUser,
    User,
    UserNotification,
)
from ...models.BaseNotificationScheduleModel import BaseNotificationScheduleModel
from ...models.NotificationEmailDelivery import NotificationEmailDeliveryStatus
from ...models.UserNotification import NotificationType
from ...models.UserNotificationUnsubscription import NotificationChannel
from ..CardChangeTransaction import dispatch_after_commit
from .EmailService import EmailService
from .UserNotificationSettingService import UserNotificationSettingService


_TModel = TypeVar(
    "_TModel",
    bound=User | Bot | Project | ProjectInvitation | ProjectWiki | Card | CardComment | Checklist | Checkitem,
)
_logger = Logger.use("notification")


type NotificationMutationAction = Literal["read", "read_all", "delete", "delete_all"]


class NotificationMutation(TypedDict):
    action: NotificationMutationAction
    unread_count: int
    notification_uid: NotRequired[str]
    read_at: NotRequired[str]


def _is_email_template_name(value: str) -> TypeGuard[TEmailTemplateName]:
    return value in get_args(TEmailTemplateName)


class NotificationService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "notification"

    def get_api_list(
        self,
        user: User,
        time_range: Literal["3d", "7d", "1m", "all"] = "3d",
        page: int = 1,
        limit: int = 20,
        unread_only: bool = False,
        authorized_projects_only: bool = False,
        *, channel: CollaborationChannel = CollaborationChannel.Api,
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Return one notification page, optionally limited to unread rows."""

        from .CardService import CardService

        if not isinstance(user, User):
            return [], False, 0
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)).first()
        if current is None or current.deleted_at or not current.activated_at:
            return [], False, 0
        contexts = self._get_service(CardService).resolve_work_visibility_contexts(current, channel)
        raw_notifications, unread_count = self.repo.user_notification.get_scoped_list(
            current, time_range, page, limit, unread_only, contexts=contexts,
        )
        has_more = len(raw_notifications) > limit
        raw_notifications = raw_notifications[:limit]

        references: list[tuple[str, int]] = []
        for notification in raw_notifications:
            references.append((notification.notifier_type, notification.notifier_id))
            for table_name, record_id in notification.record_list:
                references.append((table_name, record_id))
        cached_dict = InfraHelper.get_references(references, as_type="notification")

        notifications = []
        notification_ids_should_delete = []
        for notification in raw_notifications:
            notification_records = {}
            should_continue = True
            for table_name, record_id in notification.record_list:
                record = cached_dict.get(f"{table_name}_{record_id}")
                if not record:
                    should_continue = False
                    break
                notification_records[table_name] = record
            if not should_continue:
                notification_ids_should_delete.append(notification.id)
                continue

            notifier_cache_key = f"{notification.notifier_type}_{notification.notifier_id}"
            notifier_key = f"notifier_{notification.notifier_type}"
            notifier = cached_dict.get(notifier_cache_key)
            if not notifier:
                continue

            notifications.append(
                {
                    **notification.api_response(),
                    notifier_key: notifier,
                    "records": notification_records,
                }
            )

        if notification_ids_should_delete and not unread_only:
            self.repo.user_notification.delete_all_by_ids(notification_ids_should_delete)

        return notifications, has_more, unread_count

    def convert_to_api_response(
        self,
        notification: UserNotification,
        record_list: Sequence[_TModel] | None = None,
        notifier: TUserOrBot | None = None,
    ) -> dict[str, Any]:
        api_notification = notification.api_response()
        table_ids_dict = InfraHelper.combine_table_with_ids(notification.record_list)

        records: dict[str, Any] = {}
        if record_list:
            for record in record_list:
                table_name = type(record).__tablename__
                if table_name not in records:
                    records[table_name] = {}
                records[table_name] = record.notification_data()
        else:
            for table_name, record_ids in table_ids_dict.items():
                results = InfraHelper.get_records_by_table_name_with_ids(table_name, record_ids)
                if not results:
                    continue
                for record in results:
                    if table_name not in records:
                        records[table_name] = {}
                    records[table_name] = record.notification_data()

        api_notification["records"] = records
        if notifier:
            notifier_key, api_notifier = (
                "notifier_user" if isinstance(notifier, User) else "notifier_bot",
                notifier.api_response(),
            )
        else:
            notifier_key, api_notifier = self.get_notifier(notification, as_api=True)
        api_notification[notifier_key] = api_notifier
        return api_notification

    @overload
    def get_notifier(self, notification: UserNotification, as_api: Literal[False]) -> User | Bot: ...
    @overload
    def get_notifier(self, notification: UserNotification, as_api: Literal[True]) -> tuple[str, dict[str, Any]]: ...
    def get_notifier(self, notification: UserNotification, as_api: bool) -> User | Bot | tuple[str, dict[str, Any]]:
        if notification.notifier_type == "user":
            notifier = cast(
                User,
                InfraHelper.get_by(User, "id", notification.notifier_id, with_deleted=True),
            )
        else:
            notifier = cast(
                Bot,
                InfraHelper.get_by(Bot, "id", notification.notifier_id, with_deleted=True),
            )

        if not as_api:
            return notifier

        if notification.notifier_type == "user":
            return "notifier_user", notifier.api_response()
        return "notifier_bot", notifier.api_response()

    def read(self, user: User, notification: TNotificationParam | None) -> NotificationMutation | None:
        notification = InfraHelper.get_by_id_like(UserNotification, notification)
        if not notification or notification.receiver_id != user.id:
            return None

        notification.read_at = SafeDateTime.now()
        self.repo.user_notification.update(notification)

        return self._publish_notification_mutation(
            user,
            "read",
            notification_uid=notification.get_uid(),
            read_at=notification.read_at,
        )

    def read_all(self, user: User) -> NotificationMutation:
        read_at = SafeDateTime.now()
        self.repo.user_notification.read_all_by_user(user, read_at)
        return self._publish_notification_mutation(user, "read_all", read_at=read_at)

    def get_mentioned_card_ids(self, user: User, limit: int = 500) -> list[int]:
        """Return recent card ids behind card/comment mentions without changing read state."""

        return self.repo.user_notification.get_mentioned_card_ids(user, limit)

    def delete(self, user: User, notification: TNotificationParam | None) -> NotificationMutation | None:
        notification = InfraHelper.get_by_id_like(UserNotification, notification)
        if not notification or notification.receiver_id != user.id:
            return None

        self.repo.user_notification.delete(notification)

        return self._publish_notification_mutation(user, "delete", notification_uid=notification.get_uid())

    def delete_all(self, user: User) -> NotificationMutation:
        self.repo.user_notification.delete_all(user)
        return self._publish_notification_mutation(user, "delete_all")

    def _publish_notification_mutation(
        self,
        user: User,
        action: NotificationMutationAction,
        notification_uid: str | None = None,
        read_at: SafeDateTime | None = None,
    ) -> NotificationMutation:
        mutation: NotificationMutation = {
            "action": action,
            "unread_count": self.repo.user_notification.count_unread(user, readonly=False),
        }
        if notification_uid is not None:
            mutation["notification_uid"] = notification_uid
        if read_at is not None:
            mutation["read_at"] = read_at.isoformat()
        UserPublisher.notification_mutated(user, mutation)
        return mutation

    def recover_pending_web_fanout(self, limit: int = 50) -> int:
        pending = self.repo.user_notification.get_pending_web_fanout(SafeDateTime.now() - timedelta(seconds=30), limit)
        completed = 0
        for notification in pending:
            # Wiki delivery resolves the current account and ACL under its owner lock.
            recipient = (
                User.model_construct(id=notification.receiver_id)
                if notification.notification_type == NotificationType.MentionedInWiki
                else InfraHelper.get_by_id_like(User, notification.receiver_id)
            )
            if not recipient:
                _logger.warning("Recipient unavailable for pending web notification %s", notification.id)
                self.repo.user_notification.defer_web_fanout(notification)
                continue
            try:
                api_notification = self.convert_to_api_response(notification)
            except Exception:
                _logger.exception("Cannot rebuild pending web notification %s", notification.id)
                self.repo.user_notification.defer_web_fanout(notification)
                continue
            if not self._publish_web_notification(notification, recipient, api_notification):
                self.repo.user_notification.defer_web_fanout(notification)
                break
            completed += 1
        return completed

    def recover_pending_email_delivery(self, limit: int = 8) -> int:
        stale_cutoff = SafeDateTime.now() - timedelta(minutes=10)
        self.repo.notification_email_delivery.mark_stale_preparing_pending(stale_cutoff, limit)
        self.repo.notification_email_delivery.mark_stale_uncertain(stale_cutoff, limit)
        if not Env.MAIL_SERVER or not Env.MAIL_FROM:
            return 0

        deliveries = self.repo.notification_email_delivery.claim_pending(limit)
        completed = 0
        for delivery in deliveries:
            try:
                recipient = InfraHelper.get_by_id_like(User, delivery.receiver_id)
                unsubscribed = recipient is not None and self._get_service(
                    UserNotificationSettingService
                ).has_unsubscription(
                    recipient, delivery.notification_type, delivery.scope_models, NotificationChannel.Email
                )
            except Exception:
                _logger.exception("Cannot validate notification email recipient %s", delivery.id)
                self.repo.notification_email_delivery.complete_preparing(
                    delivery, NotificationEmailDeliveryStatus.Pending, "Recipient validation failed"
                )
                continue
            if not recipient or recipient.email != delivery.recipient_email:
                self.repo.notification_email_delivery.complete_preparing(
                    delivery, NotificationEmailDeliveryStatus.Suppressed, "Recipient is no longer available"
                )
                continue
            if unsubscribed:
                self.repo.notification_email_delivery.complete_preparing(
                    delivery, NotificationEmailDeliveryStatus.Suppressed, "Recipient unsubscribed"
                )
                continue
            if not _is_email_template_name(delivery.template_name):
                self.repo.notification_email_delivery.complete_preparing(
                    delivery, NotificationEmailDeliveryStatus.Failed, "Invalid email template"
                )
                continue

            try:
                email_service = self._get_service(EmailService)
                message = email_service.prepare_template_message(
                    delivery.preferred_lang,
                    delivery.recipient_email,
                    delivery.template_name,
                    delivery.formats,
                )
                EmailService.attach_attempt_metadata(message, source="notification", source_uid=delivery.get_uid())
            except Exception:
                _logger.exception("Cannot render notification email %s", delivery.id)
                self.repo.notification_email_delivery.complete_preparing(
                    delivery, NotificationEmailDeliveryStatus.Failed, "Email template rendering failed"
                )
                continue

            if not self.repo.notification_email_delivery.begin_sending(delivery):
                continue
            try:
                accepted = email_service.send_message(message, strict=True)
            except Exception:
                _logger.exception("Cannot confirm notification SMTP outcome %s", delivery.id)
                accepted = False

            self.repo.notification_email_delivery.complete_sending(
                delivery,
                NotificationEmailDeliveryStatus.Sent if accepted else NotificationEmailDeliveryStatus.Uncertain,
                None if accepted else "SMTP acceptance could not be confirmed",
            )
            if accepted:
                completed += 1
        return completed

    def get_email_deliveries_for_review(self, limit: int = 20) -> list[NotificationEmailDelivery]:
        if limit < 1 or limit > 100:
            raise ValueError("Review limit must be between 1 and 100")
        return self.repo.notification_email_delivery.get_review_items(limit)

    def purge_terminal_email_deliveries(self, limit: int = 100) -> int:
        retention_days = Env.NOTIFICATION_EMAIL_OUTBOX_RETENTION_DAYS
        if retention_days < 1 or limit < 1 or limit > 100:
            raise ValueError("Email outbox retention and cleanup limit must be positive")
        return self.repo.notification_email_delivery.purge_terminal_before(
            SafeDateTime.now() - timedelta(days=retention_days), limit
        )

    def resolve_email_delivery_review(
        self,
        delivery_id: SnowflakeID,
        action: str,
        ticket: str,
        acknowledge_uncertain: bool = False,
        use_current_card_title: bool = False,
    ) -> bool:
        if action not in ("retry", "confirm-sent", "close"):
            raise ValueError("Invalid email review action")
        ticket = ticket.strip()
        if not ticket or len(ticket) > 120:
            raise ValueError("A review ticket of at most 120 characters is required")
        delivery = self.repo.notification_email_delivery.get_review_item(delivery_id)
        if delivery is None or delivery.status not in (
            NotificationEmailDeliveryStatus.Failed,
            NotificationEmailDeliveryStatus.Uncertain,
        ):
            return False
        if delivery.status == NotificationEmailDeliveryStatus.Uncertain and not acknowledge_uncertain:
            raise ValueError("An uncertain SMTP outcome requires explicit acknowledgement")
        if action == "confirm-sent" and delivery.status != NotificationEmailDeliveryStatus.Uncertain:
            raise ValueError("Only an uncertain delivery can be confirmed as sent")

        formats = None
        if (
            action == "retry"
            and delivery.status == NotificationEmailDeliveryStatus.Failed
            and delivery.template_name == "reacted_to_comment"
            and "card_name" not in delivery.formats
        ):
            if not use_current_card_title:
                raise ValueError("Retry requires explicit approval to use the current Card title")
            card_ids = [record_id for model_name, record_id in (delivery.scope_models or []) if model_name == "card"]
            if len(card_ids) != 1:
                raise ValueError("Cannot identify the Card for email template repair")
            card = InfraHelper.get_by_id_like(Card, card_ids[0], with_deleted=True)
            if card is None:
                raise ValueError("Card is unavailable for email template repair")
            formats = {**delivery.formats, "card_name": escape(card.title)}
        elif use_current_card_title:
            raise ValueError("Current Card title approval applies only to missing reaction email titles")

        target_status = {
            "retry": NotificationEmailDeliveryStatus.Pending,
            "confirm-sent": NotificationEmailDeliveryStatus.ConfirmedSent,
            "close": NotificationEmailDeliveryStatus.Closed,
        }[action]
        note = f"Operator {action} under ticket {ticket}; previous outcome: {delivery.failure_reason or 'none'}"
        if formats is not None:
            note += "; current Card title approved for template repair"
        return self.repo.notification_email_delivery.resolve_review_item(
            delivery_id, delivery.status, target_status, note[:1000], formats=formats
        )

    def _publish_web_notification(
        self, notification: UserNotification, recipient: User, api_notification: dict[str, Any]
    ) -> bool:
        try:
            if notification.notification_type == NotificationType.MentionedInWiki:
                wiki_id = next(
                    (record_id for table, record_id in notification.record_list if table == "project_wiki"), None
                )
                with DbSession.atomic() as db:
                    wiki = db.exec(
                        SqlBuilder.select.table(ProjectWiki)
                        .where(
                            ProjectWiki.id == wiki_id,
                        )
                        .with_for_update()
                    ).first()
                    current = db.exec(
                        SqlBuilder.select.table(UserNotification)
                        .where(
                            UserNotification.id == notification.id,
                            UserNotification.receiver_id == recipient.id,
                        )
                        .with_for_update()
                    ).first()
                    if current is None:
                        return True
                    references = []
                    for table, record_id in current.record_list:
                        model = {"project": Project, "project_wiki": ProjectWiki}.get(table)
                        if model is None:
                            self.repo.user_notification.complete_web_fanout(current)
                            return True
                        references.append(model.model_construct(id=record_id))
                    resolved = self._resolve_notification_recipient(
                        recipient,
                        NotificationType.MentionedInWiki,
                        references,
                    )
                    if wiki is None or resolved is None or not current.web_visible:
                        self.repo.user_notification.complete_web_fanout(current)
                        return True
                    recipient, references = resolved
                    scopes = [(type(record).__tablename__, int(record.id)) for record in references]
                    if self._get_service(UserNotificationSettingService).has_unsubscription(
                        recipient,
                        NotificationType.MentionedInWiki,
                        scopes,
                        NotificationChannel.Web,
                    ):
                        self.repo.user_notification.complete_web_fanout(current)
                        return True
                    UserPublisher.notified(recipient, {**api_notification, "message_vars": current.message_vars})
                    self.repo.user_notification.complete_web_fanout(current)
                return True
            if notification.notification_type == NotificationType.BotScheduleFailed:
                # Recheck delayed/recovered delivery using current authority and source.
                references = []
                for table, record_id in notification.record_list:
                    model = {"project": Project, "card": Card}.get(table)
                    if model is None:
                        self.repo.user_notification.complete_web_fanout(notification)
                        return True
                    reference = InfraHelper.get_by_id_like(model, record_id)
                    if reference is None:
                        self.repo.user_notification.complete_web_fanout(notification)
                        return True
                    references.append(reference)
                resolved = self._resolve_notification_recipient(
                    recipient, NotificationType.BotScheduleFailed, references,
                )
                if resolved is None:
                    self.repo.user_notification.complete_web_fanout(notification)
                    return True
                recipient, references = resolved
                project = next(record for record in references if isinstance(record, Project))
                column_uid = notification.message_vars.get("project_column_uid")
                column = InfraHelper.get_by_id_like(ProjectColumn, column_uid) if isinstance(column_uid, str) else None
                if column is None or column.deleted_at or column.project_id != project.id:
                    self.repo.user_notification.complete_web_fanout(notification)
                    return True
                scopes = [("project", int(project.id)), ("project_column", int(column.id))]
                for record in references:
                    if isinstance(record, Card):
                        if record.project_column_id != column.id:
                            self.repo.user_notification.complete_web_fanout(notification)
                            return True
                        scopes.append(("card", int(record.id)))
                if project.owner_id != recipient.id or self._get_service(
                    UserNotificationSettingService
                ).has_unsubscription(recipient, NotificationType.BotScheduleFailed, scopes, NotificationChannel.Web):
                    self.repo.user_notification.complete_web_fanout(notification)
                    return True
                api_notification = self.convert_to_api_response(notification, references)
            UserPublisher.notified(recipient, api_notification)
            self.repo.user_notification.complete_web_fanout(notification)
        except Exception:
            _logger.exception("Web notification delivery remains pending: %s", notification.id)
            return False
        return True

    # from here, notifiable types are added
    def notify_project_invited(
        self,
        notifier: TUserOrBot,
        target_user: TUserParam | None,
        project: Project,
        project_invitation: ProjectInvitation,
    ):
        self.__notify(
            notifier,
            target_user,
            NotificationType.ProjectInvited,
            None,
            [project, project_invitation],
        )

    def notify_mentioned_in_card(self, notifier: TUserOrBot, project: Project, card: Card):
        column = self.__get_column_by_card(card)
        self.__notify_mentioned(
            notifier,
            card.description,
            NotificationType.MentionedInCard,
            [project, column, card],
            [project, card],
            "mentioned_in_card",
            {
                "url": self.__create_redirect_url(project, card),
                "card_name": escape(card.title),
                "project_name": escape(project.title),
            },
        )

    def notify_mentioned_in_comment(self, notifier: TUserOrBot, project: Project, card: Card, comment: CardComment):
        column = self.__get_column_by_card(card)
        self.__notify_mentioned(
            notifier,
            comment.content,
            NotificationType.MentionedInComment,
            [project, column, card],
            [project, card, comment],
            "mentioned_in_comment",
            {"url": self.__create_redirect_url(project, card), "card_name": escape(card.title)},
        )

    @staticmethod
    def redact_wiki_mentions(db, wiki: ProjectWiki, selected: str) -> None:
        """Mask exact historical mention text while the caller holds the wiki owner lock."""
        # Reuse the native notification repository's dialect-specific JSON reference
        # projection. Bound the query before loading any notification payload.
        dialect = DbEngine.get_main_engine().dialect.name
        if dialect == "sqlite":
            refs = func.json_each(UserNotification.record_list).table_valued("key", "value").alias("wiki_refs")
            table = func.json_extract(refs.c.value, "$[0]")
            record_id = func.json_extract(refs.c.value, "$[1]")
        elif dialect == "postgresql":
            refs = (
                func.jsonb_array_elements(sql_cast(UserNotification.record_list, JSONB))
                .table_valued("value")
                .alias("wiki_refs")
            )
            table = refs.c.value.op("->>")(0)
            record_id = sql_cast(refs.c.value.op("->>")(1), BigInteger)
        elif dialect in {"mysql", "mariadb"}:
            refs = (
                func.JSON_TABLE(
                    UserNotification.record_list,
                    literal_column("'$[*]' COLUMNS (ref_table VARCHAR(64) PATH '$[0]', ref_id BIGINT PATH '$[1]')"),
                )
                .table_valued("ref_table", "ref_id")
                .alias("wiki_refs")
            )
            table, record_id = refs.c.ref_table, refs.c.ref_id
        else:
            raise RuntimeError("Unsupported notification reference dialect")
        matches_wiki = (
            select(1)
            .select_from(refs)
            .where(
                table == "project_wiki",
                record_id == wiki.id,
            )
            .correlate(UserNotification)
            .exists()
        )
        after_id = 0
        while True:
            notifications = db.exec(
                SqlBuilder.select.table(UserNotification)
                .where(
                    UserNotification.notification_type == NotificationType.MentionedInWiki,
                    matches_wiki,
                    UserNotification.id > after_id,
                )
                .order_by(UserNotification.id)
                .limit(100)
            ).all()
            if not notifications:
                break
            after_id = notifications[-1].id
            for notification in notifications:
                if not any(
                    table == "project_wiki" and record_id == wiki.id for table, record_id in notification.record_list
                ):
                    continue
                line = notification.message_vars.get("line")
                if not isinstance(line, str) or selected not in line:
                    continue
                db.exec(
                    update(UserNotification)
                    .where(UserNotification.id == notification.id)
                    .values(
                        message_vars={**notification.message_vars, "line": line.replace(selected, "[••••]")},
                        updated_at=notification.updated_at,
                    )
                )

    def notify_mentioned_in_wiki(self, notifier: TUserOrBot, project: Project, wiki: ProjectWiki):
        # Serialize mention extraction and persistence with secret conversion. Queued
        # callers must never relabel a stale editor snapshot with the current generation.
        with DbSession.atomic() as db:
            # Match conversion's user-before-owner lock order for recipient FKs.
            mentioned_uids, _ = find_mentioned(wiki.content)
            recipient_ids = []
            for uid in mentioned_uids:
                try:
                    recipient_ids.append(InfraHelper.convert_id(uid))
                except (TypeError, ValueError):
                    continue
            if recipient_ids:
                db.exec(
                    SqlBuilder.select.table(User).where(User.id.in_(recipient_ids)).order_by(User.id).with_for_update()
                ).all()
            current = db.exec(
                SqlBuilder.select.table(ProjectWiki)
                .where(
                    ProjectWiki.id == wiki.id,
                    ProjectWiki.project_id == project.id,
                )
                .with_for_update()
            ).first()
            if (
                current is None
                or current.editor_generation != wiki.editor_generation
                or current.content.content != wiki.content.content
            ):
                return
            self.__notify_mentioned(
                notifier,
                current.content,
                NotificationType.MentionedInWiki,
                [project, current],
                [project, current],
                "mentioned_in_wiki",
                {"url": self.__create_redirect_url(project, current), "wiki_title": escape(current.title)},
            )

    def notify_assigned_to_card(
        self,
        notifier: TUserOrBot,
        target_user: TUserParam | None,
        project: Project,
        card: Card,
    ):
        column = self.__get_column_by_card(card)
        self.__notify(
            notifier,
            target_user,
            NotificationType.AssignedToCard,
            [project, column, card],
            [project, card],
            {},
            "assigned_to_card",
            {
                "url": self.__create_redirect_url(project, card),
                "card_name": escape(card.title),
                "project_name": escape(project.title),
            },
        )

    def notify_workflow_stage_entered(
        self, notifier: TUserOrBot, project: Project, card: Card, column: ProjectColumn, stage_key: str,
    ) -> bool:
        if card.created_by_user_id is None or (
            isinstance(notifier, User) and notifier.id == card.created_by_user_id
        ):
            return False
        return self.__notify(
            notifier, card.created_by_user_id, NotificationType.WorkflowStageEntered,
            [project, column, card], [project, card],
            {"stage_key": stage_key, "column_name": column.name},
            defer_delivery=True,
        )

    def notify_reacted_to_comment(
        self,
        notifier: TUserOrBot,
        project: Project,
        card: Card,
        comment: CardComment,
        reaction_type: str,
    ):
        column = self.__get_column_by_card(card)
        first_line = ""
        if comment.content:
            content = change_date_element(comment.content).strip().splitlines()
            first_line = content.pop() if content else ""
        self.__notify(
            notifier,
            cast(int, comment.user_id),
            NotificationType.ReactedToComment,
            [project, column, card],
            [project, card, comment],
            {"reaction_type": reaction_type, "line": first_line},
            "reacted_to_comment",
            {"url": self.__create_redirect_url(project, card), "card_name": escape(card.title)},
        )

    def notify_checklist(
        self,
        notifier: TUserOrBot,
        target_user: TUserParam | None,
        project: Project,
        card: Card,
        checklist: Checklist,
    ):
        column = self.__get_column_by_card(card)
        self.__notify(
            notifier,
            target_user,
            NotificationType.NotifiedFromChecklist,
            [project, column, card],
            [project, card, checklist],
            None,
            "notified_from_checklist",
            {
                "url": self.__create_redirect_url(project, card),
                "card_name": escape(card.title),
                "checklist_title": escape(checklist.title),
            },
        )

    def notify_notification_schedule_rule(
        self,
        notifier: TUserOrBot,
        target_user: TUserParam | None,
        notification_type: NotificationType,
        project: Project,
        rule_name: str,
        target_model: BaseNotificationScheduleModel,
        message_vars: dict[str, Any],
        now: SafeDateTime,
    ) -> bool:
        references: list[Project | Card | Checkitem] = [project]
        scope_models: list[BaseDbModel] = [project]
        if isinstance(target_model, Card):
            column = self.__get_column_by_card(target_model)
            references.append(target_model)
            scope_models.extend([column, target_model])
        elif isinstance(target_model, Checkitem):
            card = self.__get_card_by_checkitem(target_model)
            column = self.__get_column_by_card(card)
            references.extend([card, target_model])
            scope_models.extend([column, card, target_model])

        target_message_vars = target_model.get_notification_schedule_rule_message_vars(
            str(message_vars.get("field") or ""),
            str(message_vars.get("operator") or ""),
            now,
        )
        if target_message_vars is None:
            return False

        return self.__notify(
            notifier,
            target_user,
            notification_type,
            scope_models,
            references,
            {
                **message_vars,
                **target_message_vars,
                "rule_name": rule_name,
            },
            allow_self=True,
        )

    def notify_bot_schedule_failed(self, owner: User, project: Project, model: Card | ProjectColumn,
                                   message_vars: dict[str, Any]) -> bool:
        """Use the current project owner as the operational recipient; authorize every source."""
        references = [project, model] if isinstance(model, Card) else [project]
        scopes = [project, model]
        if isinstance(model, Card):
            scopes.insert(1, self.__get_column_by_card(model))
        return self.__notify(
            owner, owner, NotificationType.BotScheduleFailed, scopes, references,
            message_vars, allow_self=True, defer_delivery=True,
        )

    def create_record_list(self, record_list: Sequence[_TModel]) -> list[tuple[str, SnowflakeID]]:
        return [(type(record).__tablename__, record.id) for record in record_list]

    # to here, notifiable types are added

    def __notify_mentioned(
        self,
        notifier: TUserOrBot,
        editor: EditorContentModel | None,
        notification_type: NotificationType,
        scope_models: list[BaseDbModel],
        references: Sequence[_TModel],
        email_template_name: TEmailTemplateName,
        email_formats: dict[str, str],
    ):
        if not editor or not editor.content:
            return
        user_or_bot_uids, mentioned_lines = find_mentioned(editor)
        mentioned_in = ""
        other_models: list[BaseDbModel] = []
        if email_template_name == "mentioned_in_card":
            mentioned_in = "card"
        elif email_template_name == "mentioned_in_comment":
            mentioned_in = "comment"
            other_models = [references[-1]]
        elif email_template_name == "mentioned_in_wiki":
            mentioned_in = "project_wiki"

        for user_or_bot_uid in user_or_bot_uids:
            result = self.__notify(
                notifier,
                user_or_bot_uid,
                notification_type,
                scope_models,
                references,
                {"line": mentioned_lines[user_or_bot_uid]},
                email_template_name,
                email_formats,
            )

            if result or not mentioned_in:
                continue

            target_bot = InfraHelper.get_by_id_like(Bot, user_or_bot_uid)
            if not target_bot or target_bot.id == notifier.id:
                continue

            models = [*scope_models, *other_models]
            dispatch_after_commit(
                BotDefaultTask.bot_mentioned,
                *BotDefaultTask.mention_event_args(notifier, target_bot, mentioned_in, models),
            )

    def get_dispatch_context(self, user: User, notification_id: int) -> dict[str, Any] | None:
        """Return current recipient facts only for their authorized durable notification."""
        if not isinstance(user, User):
            return None
        with DbSession.use(readonly=False) as db:
            notification = db.exec(SqlBuilder.select.table(UserNotification).where(
                UserNotification.id == notification_id, UserNotification.receiver_id == user.id,
            )).first()
        if notification is None:
            return None
        models = {cls.__tablename__: cls for cls in (
            Project, ProjectInvitation, ProjectWiki, Card, CardComment, Checklist, Checkitem,
        )}
        references = []
        for table, record_id in notification.record_list:
            cls = models.get(table)
            if cls is None:
                return None
            references.append(cls.model_construct(id=record_id))
        resolved = self._resolve_notification_recipient(user.id, notification.notification_type, references)
        if resolved is None:
            return None
        current, _ = resolved
        return {"email": current.email, "preferred_lang": current.preferred_lang, "firstname": current.firstname}

    def can_dispatch_work_event(self, model) -> bool:
        """Revalidate the durable source and its recipient at each outbound attempt."""
        from ....tasks.webhooks.utils import build_notification_work_event

        uid = model.data.get("notification_uid") if isinstance(model.data, dict) else None
        if not isinstance(uid, str):
            return False
        try:
            notification_id = SnowflakeID.from_short_code(uid)
        except (ValueError, TypeError):
            return False
        with DbSession.use(readonly=False) as db:
            notification = db.exec(SqlBuilder.select.table(UserNotification).where(
                UserNotification.id == notification_id,
            )).first()
        if notification is None:
            return False
        expected = build_notification_work_event(notification)
        if expected is None or expected.model_dump(exclude={"occurred_at"}) != model.model_dump(exclude={"occurred_at"}):
            return False
        try:
            def utc_timestamp(value: str):
                parsed = datetime.fromisoformat(value)
                return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            if utc_timestamp(expected.occurred_at) != utc_timestamp(model.occurred_at):
                return False
        except (TypeError, ValueError):
            return False
        models = {cls.__tablename__: cls for cls in (
            Project, ProjectInvitation, ProjectWiki, Card, CardComment, Checklist, Checkitem,
        )}
        references = []
        for table, record_id in notification.record_list:
            cls = models.get(table)
            if cls is None:
                return False
            references.append(cls.model_construct(id=record_id))
        return self._resolve_notification_recipient(
            notification.receiver_id, notification.notification_type, references,
        ) is not None

    def _resolve_notification_recipient(
        self, target_user: TUserParam | None, notification_type: NotificationType,
        references: list[_TModel],
    ) -> tuple[User, list[_TModel]] | None:
        """Authorize current references before persisting or scheduling outbound content."""
        from .CardService import CardService

        user_id = InfraHelper.convert_id(target_user) if target_user is not None else None
        if not user_id or not references:
            return None
        allowed_models = (Project, ProjectInvitation, ProjectWiki, Card, CardComment, Checklist, Checkitem)
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(User).where(User.id == user_id)).first()
            if current is None or current.deleted_at or not current.activated_at:
                return None
            canonical = []
            for reference in references:
                model = type(reference)
                if model not in allowed_models:
                    return None
                record = db.exec(SqlBuilder.select.table(model).where(model.id == reference.id)).first()
                if record is None or getattr(record, "deleted_at", None):
                    return None
                canonical.append(record)
            projects = [record for record in canonical if isinstance(record, Project)]
            if len(projects) != 1:
                return None
            project = projects[0]
            contexts = self._get_service(CardService).resolve_work_visibility_contexts(current, CollaborationChannel.Api)
            context = contexts.get(int(project.id))
            invitation = notification_type == NotificationType.ProjectInvited
            if invitation:
                invites = [record for record in canonical if isinstance(record, ProjectInvitation)]
                if len(canonical) != 2 or len(invites) != 1:
                    return None
                if invites[0].project_id != project.id or invites[0].email.casefold() != current.email.casefold():
                    return None
            elif context is None:
                return None
            for record in canonical:
                if isinstance(record, Project):
                    continue
                if isinstance(record, ProjectInvitation):
                    if not invitation:
                        return None
                    continue
                if isinstance(record, ProjectWiki):
                    assigned = db.exec(SqlBuilder.select.table(ProjectWikiAssignedUser).where(
                        ProjectWikiAssignedUser.project_wiki_id == record.id,
                        ProjectWikiAssignedUser.user_id == current.id,
                    )).first()
                    if record.project_id != project.id or not (record.is_public or project.owner_id == current.id or assigned):
                        return None
                    continue
                card = record if isinstance(record, Card) else None
                if isinstance(record, Checkitem):
                    checklist = db.exec(SqlBuilder.select.table(Checklist).where(Checklist.id == record.checklist_id)).first()
                    if checklist is None or checklist.deleted_at:
                        return None
                    card_id = checklist.card_id
                elif isinstance(record, (CardComment, Checklist)):
                    card_id = record.card_id
                else:
                    card_id = record.id
                if card is None:
                    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card_id)).first()
                if card is None or card.deleted_at or card.project_id != project.id or context is None:
                    return None
                if not context.can_read_card(card.visibility, owner_user_id=card.owner_user_id):
                    return None
        return current, canonical

    def __notify(
        self,
        notifier: TUserOrBot,
        target_user: TUserParam | None,
        notification_type: NotificationType,
        scope_models: list[BaseDbModel] | None,
        references: Sequence[_TModel],
        message_vars: dict[str, Any] | None = None,
        email_template_name: TEmailTemplateName | None = None,
        email_formats: dict[str, str] | None = None,
        allow_self: bool = False,
        defer_delivery: bool = False,
    ) -> bool:
        resolved = self._resolve_notification_recipient(target_user, notification_type, references)
        if resolved is None:
            return False
        target_user, references = resolved
        if isinstance(notifier, User) and target_user.id == notifier.id and not allow_self:
            return False

        raw_record_list = self.create_record_list(references)
        record_list = [(table_name, SnowflakeID(record_id)) for table_name, record_id in raw_record_list]

        scope_model_tuples = (
            [(type(scope_model).__tablename__, int(scope_model.id)) for scope_model in scope_models]
            if scope_models
            else None
        )

        email_enabled = bool(
            email_template_name
            and target_user.email
            and not self._get_service(UserNotificationSettingService).has_unsubscription(
                target_user, notification_type, scope_model_tuples, NotificationChannel.Email
            )
        )
        if email_enabled and not Env.NOTIFICATION_EMAIL_OUTBOX_ENABLED:
            raise RuntimeError("Notification email outbox must be enabled")

        if email_formats:
            email_formats["recipient"] = target_user.firstname
            email_formats["sender"] = notifier.get_fullname()

        notification = UserNotification(
            notifier_type="user" if isinstance(notifier, User) else "bot",
            notifier_id=notifier.id,
            receiver_id=target_user.id,
            notification_type=notification_type,
            message_vars=message_vars or {},
            record_list=record_list,
        )

        web_enabled = isinstance(notifier, User) and not self._get_service(
            UserNotificationSettingService
        ).has_unsubscription(target_user, notification_type, scope_model_tuples, NotificationChannel.Web)
        # Retain hidden notifications for durable work events; visibility governs UI delivery.
        notification.web_visible = web_enabled
        notification.web_fanout_pending = web_enabled

        email_delivery = None
        if email_enabled and email_template_name:
            email_delivery = NotificationEmailDelivery(
                notification_id=notification.id,
                receiver_id=target_user.id,
                notification_type=notification_type,
                scope_models=scope_model_tuples,
                recipient_email=target_user.email,
                preferred_lang=target_user.preferred_lang,
                template_name=email_template_name,
                formats=email_formats or {},
            )

        if email_delivery is not None:
            self.repo.notification_email_delivery.accept(email_delivery, notification)
        else:
            self.repo.user_notification.insert(notification)

        def deliver():
            publish_pending_work_events()
            if web_enabled:
                api_notification = self.convert_to_api_response(notification, references, notifier)
                self._publish_web_notification(notification, target_user, api_notification)

        if defer_delivery or DbSession.has_active_transaction():
            notification, target_user, references, notifier = deepcopy((notification, target_user, references, notifier))
            with DbSession.atomic() as db:
                db.after_commit(deliver)
        else:
            deliver()
        return True

    def __create_redirect_url(self, project: Project, card_or_wiki: ProjectWiki | Card | None = None):
        url_chunks = urlparse(Env.UI_REDIRECT_URL)
        query_string = ""
        if card_or_wiki:
            chunk_query = (
                UI_QUERY_NAMES.BOARD_CARD_CHUNK if isinstance(card_or_wiki, Card) else UI_QUERY_NAMES.BOARD_WIKI_CHUNK
            )
            main_query = UI_QUERY_NAMES.BOARD_CARD if isinstance(card_or_wiki, Card) else UI_QUERY_NAMES.BOARD_WIKI
            query_string = concat(
                chunk_query.value,
                "=",
                project.get_uid(),
                "&",
                main_query.value,
                "=",
                card_or_wiki.get_uid(),
            )
        else:
            query_string = concat(UI_QUERY_NAMES.BOARD.value, "=", project.get_uid())
        url = url_chunks._replace(
            query=concat(
                url_chunks.query,
                "&" if url_chunks.query else "",
                query_string,
            )
        ).geturl()

        return url

    def __get_column_by_card(self, card: Card):
        column = InfraHelper.get_by_id_like(ProjectColumn, card.project_column_id)
        return cast(ProjectColumn, column)

    def __get_card_by_checkitem(self, checkitem: Checkitem):
        checklist = InfraHelper.get_by_id_like(Checklist, checkitem.checklist_id)
        return cast(Card, InfraHelper.get_by_id_like(Card, checklist.card_id if checklist else None))
