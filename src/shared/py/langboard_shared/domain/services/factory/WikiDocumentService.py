"""Native wiki attachment documents using the shared generation lifecycle."""

from os import devnull
from sqlalchemy import update
from sqlalchemy.orm import defer
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.storage import Storage, StorageName
from ....helpers import InfraHelper
from ...models import (
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
    WikiAttachmentDocument,
)
from ..AppGovernance import lock_policy_mutation
from .ProjectDocumentService import ProjectDocumentService
from .ProjectWikiService import ProjectWikiService


class WikiDocumentService(ProjectDocumentService):
    document_model = WikiAttachmentDocument
    source_type = "wiki_attachment"
    index_task = "langboard_shared.tasks.docling.WikiDocumentTask.index_wiki_attachment"

    @staticmethod
    def name():
        return "wiki_document"

    @staticmethod
    def source_revision(attachment):
        from hashlib import sha256

        return sha256(
            (ProjectDocumentService.source_revision(attachment) + ":" + str(attachment.project_wiki_id)).encode()
        ).hexdigest()

    def vector_source(self, board, attachment):
        return {
            **super().vector_source(board, attachment),
            "wiki_uid": InfraHelper.convert_uid(attachment.project_wiki_id),
        }

    def _authorized_source(self, db, user, project, attachment, *, lock=False):
        if not isinstance(user, User):
            return None
        project_id = InfraHelper.convert_id(project)
        if lock:
            lock_policy_mutation(db)
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                db.exec(update(Project).where(Project.id == project_id).values(id=Project.id))
            for model, conditions in (
                (User, (User.id == user.id,)),
                (Project, (Project.id == project_id,)),
                (
                    ProjectAssignedUser,
                    (ProjectAssignedUser.project_id == project_id, ProjectAssignedUser.user_id == user.id),
                ),
                (ProjectRole, (ProjectRole.project_id == project_id, ProjectRole.user_id == user.id)),
            ):
                db.exec(SqlBuilder.select.table(model).where(*conditions).with_for_update()).all()
        query = (
            SqlBuilder.select.table(ProjectWikiAttachment)
            .where(ProjectWikiAttachment.id == InfraHelper.convert_id(attachment))
            .execution_options(populate_existing=True)
        )
        source = db.exec(query).first()
        if source is None or source.file.storage_name != StorageName.Wiki.value:
            return None
        wiki_query = (
            SqlBuilder.select.table(ProjectWiki)
            .where(ProjectWiki.id == source.project_wiki_id, ProjectWiki.project_id == project_id)
            .options(defer(ProjectWiki.content, raiseload=True))
            .execution_options(populate_existing=True)
        )
        wiki = db.exec(wiki_query.with_for_update() if lock else wiki_query).first()
        if wiki is None:
            return None
        if lock:
            # Match deletion lock order: project, wiki, attachment, document.
            source = db.exec(query.with_for_update()).first()
            if source is None or source.project_wiki_id != wiki.id:
                return None
            db.exec(
                SqlBuilder.select.table(ProjectWikiAssignedUser)
                .where(
                    ProjectWikiAssignedUser.project_wiki_id == wiki.id,
                    ProjectWikiAssignedUser.user_id == user.id,
                )
                .with_for_update()
            ).all()
        readable = self._get_service(ProjectWikiService).resolve_readable_file(
            user, source.file.storage_type, source.file.storage_name, source.file.filename, metadata_only=True
        )
        if readable is None or readable.id != source.id:
            return None
        board = db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id)).first()
        return (board, source) if board else None

    def request(self, user, project, attachment, *, automatic=False, force=False):
        # Historical rows acquire a bounded, authoritative hash only on an authorized request.
        with DbSession.use(readonly=False) as db:
            source = self._authorized_source(db, user, project, attachment)
            if source is None:
                return None
            board, current = source
            if not current.content_sha256:
                identity = current.file.model_dump()
                digest = self.hash_file(current.file)
                with DbSession.atomic() as locked:
                    refreshed = self._authorized_source(locked, user, board, current, lock=True)
                    if refreshed is None or refreshed[1].file.model_dump() != identity:
                        return None
                    current = refreshed[1]
                    if not current.content_sha256:
                        current.content_sha256 = digest
                        locked.update(current)
        return super().request(user, project, attachment, automatic=automatic, force=force)

    @staticmethod
    def hash_file(file):
        from ....Env import Env
        from ....tasks.docling.DoclingMetadataTask import _BoundedDownload

        with open(devnull, "wb") as sink:
            writer = _BoundedDownload(sink, Env.MAX_FILE_SIZE_MB * 1024 * 1024)
            if not Storage.download_file(file, writer):
                raise ValueError("Wiki attachment content unavailable")
            return writer.digest.hexdigest()

    def _load_source(self, db, attachment_uid, generation, *, lock=False):
        hint = db.exec(
            SqlBuilder.select.table(ProjectWikiAttachment).where(
                ProjectWikiAttachment.id == InfraHelper.convert_id(attachment_uid)
            )
        ).first()
        if hint is None:
            return None
        document_hint = self._document(db, hint)
        wiki = db.exec(
            SqlBuilder.select.table(ProjectWiki)
            .where(ProjectWiki.id == hint.project_wiki_id)
            .options(defer(ProjectWiki.content, raiseload=True))
        ).first()
        if document_hint is None or wiki is None:
            return None
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == document_hint.requested_by_id)).first()
        source = self._authorized_source(db, actor, wiki.project_id, hint, lock=lock)
        if source is None:
            return None
        from .DoclingMetadataService import DoclingMetadataService

        document = self._document(db, source[1], lock=lock)
        if (
            document is None
            or document.requested_by_id != actor.id
            or (generation is not None and document.generation != generation)
            or document.source_revision != self.source_revision(source[1])
            or not self._get_service(DoclingMetadataService).detect_document_type(source[1].filename)
        ):
            return None
        return *source, document

    def record_scope_embedding_cleanup(self, project, wiki=None):
        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            db.exec(SqlBuilder.select.table(Project).where(Project.id == project.id).with_for_update()).first()
            query = SqlBuilder.select.table(ProjectWiki, with_deleted=True).where(ProjectWiki.project_id == project.id)
            if wiki is not None:
                query = query.where(ProjectWiki.id == InfraHelper.convert_id(wiki))
            wikis = db.exec(query.order_by(ProjectWiki.id).with_for_update()).all()
            for current in wikis:
                attachments = db.exec(
                    SqlBuilder.select.table(ProjectWikiAttachment, with_deleted=True)
                    .where(ProjectWikiAttachment.project_wiki_id == current.id)
                    .order_by(ProjectWikiAttachment.id)
                    .with_for_update()
                ).all()
                for attachment in attachments:
                    self.invalidate(db, attachment)
