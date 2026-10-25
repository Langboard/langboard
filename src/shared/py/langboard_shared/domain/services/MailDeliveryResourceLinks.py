"""Detail-only references from native outbox relations and current board authority."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import Card, NotificationEmailDelivery, Project, ProjectActivity, ProjectActivityEmailDelivery
from ..models.MailDeliveryAttempt import MailDeliveryAttempt
from ..models.ProjectRole import ProjectRoleAction
from .CardCrossBoardOperations import _grant


class MailDeliveryResourceLinks:
    @staticmethod
    def resolve(source, uid, user, service, channel):
        # The detail reader has already validated this native UID. Never consume linked_ids or formats.
        identifier = SnowflakeID.from_short_code(uid)
        with DbSession.use(readonly=False, sensitive=True) as db:
            if source == "smtp_attempt":
                relation = db.exec(
                    SqlBuilder.select.columns(MailDeliveryAttempt.source, MailDeliveryAttempt.source_uid)
                    .where(MailDeliveryAttempt.id == identifier)
                    .limit(1)
                ).first()
                if not relation or relation[0] not in ("notification", "project_activity"):
                    return []
                source, uid = relation
                if not isinstance(uid, str) or len(uid) != 11 or not uid.isascii() or not uid.isalnum():
                    return []
                identifier = SnowflakeID.from_short_code(uid)
                if identifier <= 0 or identifier.to_short_code() != uid:
                    return []
            project_id, card_id = None, None
            if source == "notification":
                row = db.exec(
                    SqlBuilder.select.columns(NotificationEmailDelivery.scope_models)
                    .where(NotificationEmailDelivery.id == identifier)
                    .limit(1)
                ).first()
                if row is None:
                    return []
                scopes = row[0]
                if not isinstance(scopes, list):
                    return []
                references = {}
                for pair in scopes:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        return []
                    name, value = pair
                    if name not in (Project.__tablename__, Card.__tablename__):
                        continue
                    if type(value) is not int or not 0 < value <= SnowflakeID.MAX_VALUE or name in references:
                        return []
                    references[name] = value
                project_id, card_id = references.get(Project.__tablename__), references.get(Card.__tablename__)
            elif source == "project_activity":
                row = db.exec(
                    SqlBuilder.select.columns(ProjectActivity.project_id, ProjectActivity.card_id)
                    .select_from(ProjectActivityEmailDelivery)
                    .join(ProjectActivity, ProjectActivity.id == ProjectActivityEmailDelivery.activity_id)
                    .where(
                        ProjectActivityEmailDelivery.id == identifier,
                        ProjectActivityEmailDelivery.activity_table == ProjectActivity.__tablename__,
                        ProjectActivityEmailDelivery.project_id == ProjectActivity.project_id,
                    )
                    .limit(1)
                ).first()
                if row is None:
                    return []
                project_id, card_id = row
            else:
                return []
            if card_id is not None:
                resolved = service.card.resolve_readable_card(project_id, card_id, user, channel, metadata_only=True)
                if resolved is None:
                    return []
                project, card, _ = resolved
                if not _grant(db, user, project, ProjectRoleAction.Read):
                    return []
                return [{"kind": "card", "project_uid": project.get_uid(), "card_uid": card.get_uid()}]
            if project_id is None:
                return []
            resolved = service.card.resolve_visibility_context(project_id, user, channel)
            if resolved is None:
                return []
            project, context = resolved
            if not context.project_member or not _grant(db, user, project, ProjectRoleAction.Read):
                return []
            return [{"kind": "board", "project_uid": project.get_uid()}]
