"""Source-authorized cross-board card links over the native linked-resource contract."""

from sqlalchemy.exc import IntegrityError
from ...core.db import DbSession, EditorContentModel, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...helpers import InfraHelper
from ...publishers import CardPublisher
from ...tasks.activities import CardActivityTask
from ..models import Card, CardAppOwnership, Project, ProjectColumn, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction


def _grant(db, actor, project, action):
    """Require a current account, membership and explicit native board grant."""
    current = db.exec(
        SqlBuilder.select.table(User).where(User.id == actor.id).execution_options(populate_existing=True)
    ).first()
    if current is None or current.deleted_at is not None or not current.activated_at:
        return False
    if project.owner_id == current.id or current.is_admin:
        return True
    role = db.exec(
        SqlBuilder.select.table(ProjectRole)
        .where(ProjectRole.project_id == project.id, ProjectRole.user_id == current.id)
        .execution_options(populate_existing=True)
    ).first()
    return bool(role and role.is_granted(action))


def readable_original(service, actor, source_uid, channel, *, metadata_only=False):
    if not isinstance(actor, User):
        return None
    resolved = service.resolve_readable_card(None, source_uid, actor, channel, metadata_only=metadata_only)
    if resolved is None:
        return None
    project, source, _ = resolved
    with DbSession.use(readonly=False) as db:
        if not _grant(db, actor, project, ProjectRoleAction.Read):
            return None
        ownership = db.exec(
            SqlBuilder.select.table(CardAppOwnership)
            .where(CardAppOwnership.card_id == source.id)
            .execution_options(populate_existing=True)
        ).first()
    if source.is_linked_resource or (ownership is not None and ownership.app_key is not None):
        return None
    return project, source


def project_card_payloads(service, actor, cards, *, include_content, channel, destination_project):
    payloads = {}
    if not cards:
        return payloads
    destination = service.resolve_visibility_context(destination_project, actor, channel)
    with DbSession.use(readonly=False) as db:
        destination_allowed = bool(
            destination is not None
            and destination[1].project_member
            and _grant(db, actor, destination[0], ProjectRoleAction.Read)
        )
    for mirror in cards:
        payload = {"type": Card.LINKED_RESOURCE_PROJECT_CARD, "uid": mirror.source_uid, "status": "forbidden"}
        original = (
            readable_original(service, actor, mirror.source_uid, channel, metadata_only=not include_content)
            if destination_allowed else None
        )
        if original is not None:
            project, source = original
            payload.update(
                status="available",
                title=source.title,
                project_uid=project.get_uid(),
                url=f"/board/{project.get_uid()}/{source.get_uid()}",
                allowed_actions=["open_original", "remove_mirror"],
            )
            if include_content:
                payload["content"] = source.description.model_dump()
                payload["preview"] = source.description.content[: service.LINKED_RESOURCE_PREVIEW_MAX_LENGTH]
        payloads[mirror.get_uid()] = payload
    return payloads


def link_card(
    service,
    actor,
    source_project,
    source_card,
    destination_project,
    destination_column,
    *,
    channel=CollaborationChannel.Api,
):
    """Create one reference and its provenance atomically; retries return the same reference."""
    if not isinstance(actor, User):
        return None
    with DbSession.atomic() as db:
        # Stable project ordering serializes destination order allocation and competing links.
        project_ids = sorted({InfraHelper.convert_id(source_project), InfraHelper.convert_id(destination_project)})
        projects = db.exec(
            SqlBuilder.select.table(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        by_id = {p.id: p for p in projects if p.deleted_at is None}
        source_board = by_id.get(InfraHelper.convert_id(source_project))
        destination = by_id.get(InfraHelper.convert_id(destination_project))
        if source_board is None or destination is None or source_board.id == destination.id:
            return None
        source = db.exec(
            SqlBuilder.select.table(Card)
            .where(Card.id == InfraHelper.convert_id(source_card))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        column = db.exec(
            SqlBuilder.select.table(ProjectColumn)
            .where(ProjectColumn.id == InfraHelper.convert_id(destination_column))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if (
            source is None
            or source.project_id != source_board.id
            or source.archived_at
            or column is None
            or column.deleted_at is not None
            or column.project_id != destination.id
            or column.is_archive
        ):
            return None
        original = readable_original(service, actor, source.get_uid(), channel)
        destination_context = service.resolve_visibility_context(destination, actor, channel)
        if (
            original is None
            or destination_context is None
            or not destination_context[1].project_member
            or not _grant(db, actor, destination, ProjectRoleAction.CardWrite)
        ):
            return None
        existing = service.repo.card.find_linked_resource(
            destination, Card.LINKED_RESOURCE_PROJECT_CARD, source.get_uid()
        )
        created = False
        if existing is None:
            mirror = Card(
                project_id=destination.id,
                project_column_id=column.id,
                title="",
                description=EditorContentModel(),
                created_by_user_id=actor.id,
                visibility=source.visibility,
                owner_user_id=source.owner_user_id,
                order=service.repo.card.get_next_order(column, {"project_id": destination.id}),
                source_type=Card.LINKED_RESOURCE_PROJECT_CARD,
                source_uid=source.get_uid(),
            )
            try:
                with db.savepoint():
                    db.insert(mirror)
                    CardActivityTask.record_card_linked(actor, destination, mirror, source_board, source)
                existing, created = mirror, True
            except IntegrityError:
                existing = service.repo.card.find_linked_resource(
                    destination, Card.LINKED_RESOURCE_PROJECT_CARD, source.get_uid()
                )
                if existing is None:
                    raise
            if created:
                # Common board sockets must never carry a viewer-specific source projection.
                broadcast = existing.board_api_response(0, [], [], [])
                broadcast["linked_resource"] = {
                    "type": Card.LINKED_RESOURCE_PROJECT_CARD,
                    "uid": source.get_uid(),
                    "status": "forbidden",
                }
                db.after_commit(lambda: CardPublisher.created(destination, column, {"card": broadcast}))
        payload = existing.board_api_response(0, [], [], [])
        payload["linked_resource"] = project_card_payloads(
            service, actor, [existing], include_content=False, channel=channel, destination_project=destination
        )[existing.get_uid()]
        return existing, payload, created
