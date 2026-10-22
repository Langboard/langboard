"""Current project authorization and exact file provenance on the primary database."""

from sqlalchemy import Text, cast, func, literal_column
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.storage import StorageName
from ....helpers import InfraHelper
from ...models import Project, ProjectAssignedUser, ProjectAttachment, ProjectRole, User
from ...models.ProjectRole import ProjectRoleAction


def current_authority(db, user, project):
    if not isinstance(user, User):
        return None
    actor = db.exec(
        SqlBuilder.select.table(User).where(User.id == user.id).execution_options(populate_existing=True)
    ).first()
    board = db.exec(
        SqlBuilder.select.table(Project)
        .where(Project.id == InfraHelper.convert_id(project))
        .execution_options(populate_existing=True)
    ).first()
    if actor is None or not actor.activated_at or board is None:
        return None
    member = db.exec(
        SqlBuilder.select.table(ProjectAssignedUser).where(
            ProjectAssignedUser.project_id == board.id, ProjectAssignedUser.user_id == actor.id
        )
    ).first()
    role = db.exec(
        SqlBuilder.select.table(ProjectRole)
        .where(ProjectRole.project_id == board.id, ProjectRole.user_id == actor.id)
        .execution_options(populate_existing=True)
    ).first()
    # Ownership is a native membership; administrator alone never grants membership.
    if not (board.owner_id == actor.id or member is not None):
        return None
    if not (actor.is_admin or board.owner_id == actor.id or role and role.is_granted(ProjectRoleAction.Read)):
        return None
    return actor, board, role


def replacement_allowed(authority, attachment):
    actor, board, role = authority
    return attachment.project_id == board.id and bool(
        attachment.user_id == actor.id
        or actor.is_admin
        or board.owner_id == actor.id
        or role
        and role.is_granted(ProjectRoleAction.Update)
    )


def file_identity(storage_type, storage_name, filename):
    column = ProjectAttachment.column("file")
    dialect = DbEngine.get_main_engine().dialect.name
    if dialect == "postgresql":
        decoded = cast(cast(column, JSONB).op("#>>")(cast([], ARRAY(Text))), JSONB)

        def value(key):
            return decoded.op("->>")(literal_column(f"'{key}'"))
    elif dialect == "sqlite":

        def value(key):
            return func.json_extract(func.json_extract(column, literal_column("'$'")), literal_column(f"'$.{key}'"))
    elif dialect in {"mysql", "mariadb"}:

        def value(key):
            return func.json_unquote(func.json_extract(func.json_unquote(column), f"$.{key}"))
    else:
        raise ValueError("Unsupported database dialect for file provenance")
    return (value("storage_type") == storage_type, value("storage_name") == storage_name, value("filename") == filename)


class ProjectAttachmentService(BaseDomainService):
    @staticmethod
    def name():
        return "project_attachment"

    def upload(self, user, project, upload, **kwargs):
        from ..ProjectAttachmentUploadOperations import upload_attachment

        return upload_attachment(self, user, project, upload, **kwargs)

    def can_upload(self, user, project):
        with DbSession.use(readonly=False) as db:
            return current_authority(db, user, project) is not None

    def can_replace(self, user, project, attachment):
        with DbSession.use(readonly=False) as db:
            authority = current_authority(db, user, project)
            current = db.exec(
                SqlBuilder.select.table(ProjectAttachment)
                .where(ProjectAttachment.id == InfraHelper.convert_id(attachment))
                .execution_options(populate_existing=True)
            ).first()
            return bool(authority and current and replacement_allowed(authority, current))

    def resolve_file_owner(self, storage_type, storage_name, filename):
        if storage_name != StorageName.ProjectAttachment.value:
            return None
        with DbSession.use(readonly=False) as db:
            # Include deleted rows: reusing an old object's key is ambiguous provenance.
            matches = db.exec(
                SqlBuilder.select.table(ProjectAttachment, with_deleted=True)
                .where(*file_identity(storage_type, storage_name, filename))
                .limit(2)
                .execution_options(populate_existing=True)
            ).all()
            if len(matches) != 1:
                return None
            owner = matches[0]
            if owner.deleted_at is None:
                return owner if owner.replaced_by_id is None else None
            if owner.replaced_by_id is None or owner.replaced_by_id == owner.id:
                return None
            target = db.exec(
                SqlBuilder.select.table(ProjectAttachment)
                .where(
                    ProjectAttachment.id == owner.replaced_by_id,
                    ProjectAttachment.project_id == owner.project_id,
                    ProjectAttachment.user_id == owner.user_id,
                    ProjectAttachment.replaced_by_id.is_(None),
                )
                .execution_options(populate_existing=True)
            ).first()
            if target is None or target.file.storage_name != StorageName.ProjectAttachment.value:
                return None
            owners = db.exec(
                SqlBuilder.select.table(ProjectAttachment, with_deleted=True)
                .where(*file_identity(target.file.storage_type, target.file.storage_name, target.file.filename))
                .limit(2)
            ).all()
            return target if len(owners) == 1 and owners[0].id == target.id else None

    def resolve_readable_file(self, user, storage_type, storage_name, filename):
        attachment = self.resolve_file_owner(storage_type, storage_name, filename)
        if attachment is None:
            return None
        with DbSession.use(readonly=False) as db:
            return attachment if current_authority(db, user, attachment.project_id) else None
