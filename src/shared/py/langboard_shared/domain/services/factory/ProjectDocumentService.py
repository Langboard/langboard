"""Native project document authorization, generation fencing and publication."""

from copy import deepcopy
from hashlib import sha256
from json import dumps
from uuid import uuid4
from sqlalchemy import delete, update
from ....core.broker import Broker
from ....core.broker.TaskParameters import TaskParameters
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....helpers import InfraHelper
from ....tasks.docling.DocumentKeywords import KEYWORD_LANGUAGES, normalize_keywords
from ...models import Project, ProjectAssignedUser, ProjectAttachment, ProjectAttachmentDocument, ProjectRole, User
from ..AppGovernance import lock_policy_mutation
from .CardAttachmentService import CardAttachmentService
from .DoclingMetadataService import DoclingMetadataService
from .InternalBotService import InternalBotService
from .ProjectAttachmentService import current_authority, file_identity


INDEX_TASK = "langboard_shared.tasks.docling.ProjectDocumentTask.index_project_attachment"
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


class ProjectDocumentService(BaseDomainService):
    document_model = ProjectAttachmentDocument
    source_type = "project_attachment"
    index_task = INDEX_TASK

    def vector_source(self, board, attachment):
        return {
            "source_type": self.source_type,
            "board_uid": board.get_uid(),
            "attachment_uid": attachment.get_uid(),
            "content_hash": attachment.content_sha256,
        }

    @staticmethod
    def name():
        return "project_document"

    @staticmethod
    def source_revision(attachment):
        identity = [
            attachment.get_uid(),
            attachment.file.storage_type,
            attachment.file.storage_name,
            attachment.file.filename,
            attachment.content_sha256,
        ]
        return sha256(dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    def _authorized_source(self, db, user, project, attachment, *, lock=False):
        if not isinstance(user, User):
            return None
        project_id = InfraHelper.convert_id(project)
        if lock:
            lock_policy_mutation(db)
            # SQLite lacks row locks. Acquire its writer reservation before reading state.
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
        authority = current_authority(db, user, project_id)
        if authority is None:
            return None
        source_query = (
            SqlBuilder.select.table(ProjectAttachment)
            .where(
                ProjectAttachment.id == InfraHelper.convert_id(attachment), ProjectAttachment.project_id == project_id
            )
            .execution_options(populate_existing=True)
        )
        source = db.exec(source_query.with_for_update() if lock else source_query).first()
        if source is None or source.replaced_by_id is not None:
            return None
        from ....core.storage import StorageName

        if source.file.storage_name != StorageName.ProjectAttachment.value:
            return None
        owners = db.exec(
            SqlBuilder.select.table(ProjectAttachment, with_deleted=True)
            .where(*file_identity(source.file.storage_type, source.file.storage_name, source.file.filename))
            .limit(2)
        ).all()
        if len(owners) != 1 or owners[0].id != source.id or owners[0].deleted_at is not None:
            return None
        return authority[1], source

    def _document(self, db, attachment, *, lock=False):
        model = self.document_model
        query = (
            SqlBuilder.select.table(model)
            .where(model.attachment_id == attachment.id)
            .execution_options(populate_existing=True)
        )
        return db.exec(query.with_for_update() if lock else query).first()

    def request(self, user, project, attachment, *, automatic=False, force=False):
        with DbSession.atomic() as db:
            source = self._authorized_source(db, user, project, attachment, lock=True)
            if source is None:
                return None
            board, attachment = source
            internal = self._get_service(InternalBotService)
            if automatic and not internal.is_document_processing_enabled():
                return None
            binding = internal.get_document_vision_binding()
            if binding is None:
                if automatic:
                    return None
                raise ValueError("Configure a document vision provider before processing attachments")
            if not self._get_service(DoclingMetadataService).detect_document_type(attachment.filename):
                if automatic:
                    return None
                raise ValueError("Attachment format does not support document processing")
            card_service = self._get_service(CardAttachmentService)
            vision = card_service._snapshot_vision_config(binding)
            embedding_config = card_service._snapshot_embedding_config()
            revision = self.source_revision(attachment)
            document = self._document(db, attachment, lock=True)
            requester_valid = True
            if document is not None and document.status in {"pending", "processing"}:
                previous_actor = db.exec(
                    SqlBuilder.select.table(User)
                    .where(User.id == document.requested_by_id)
                    .execution_options(populate_existing=True)
                ).first()
                requester_valid = self._authorized_source(db, previous_actor, board, attachment) is not None
            if (
                document
                and requester_valid
                and not force
                and document.source_revision == revision
                and document.vision_config == vision
                and document.embedding_config == embedding_config
                and document.status in {"pending", "processing", "indexed"}
            ):
                return self._receipt(attachment, document)
            if document is None:
                document = self.document_model(
                    attachment_id=attachment.id,
                    requested_by_id=user.id,
                    source_revision=revision,
                    generation=uuid4().hex,
                    status="pending",
                    vision_config=vision,
                    embedding_config=embedding_config,
                )
                db.insert(document)
            else:
                self._preserve_published(attachment, document)
                document.requested_by_id = user.id
                document.source_revision = revision
                document.generation = uuid4().hex
                document.status = "pending"
                document.vision_config = vision
                document.embedding_config = embedding_config
                document.error = None
                db.update(document)
            attachment_uid, generation = attachment.get_uid(), document.generation
            args, kwargs = TaskParameters(dumps({"attachment_uid": attachment_uid, "generation": generation})).pack()

            def enqueue():
                try:
                    Broker.celery.send_task(self.index_task, args=args, kwargs=kwargs)
                except Exception:
                    self.mark_failed(attachment_uid, generation, phase="extraction", only_pending=True)

            db.after_commit(enqueue)
            return self._receipt(attachment, document)

    @staticmethod
    def _receipt(attachment, document):
        return {"attachment_uid": attachment.get_uid(), "generation": document.generation, "status": document.status}

    def get_readable(self, user, project, attachment):
        with DbSession.use(readonly=False) as db:
            source = self._authorized_source(db, user, project, attachment)
            if source is None:
                return None
            board, attachment = source
            document = self._document(db, attachment)
            if document is None or document.source_revision != self.source_revision(attachment):
                return None
            return board, attachment, document

    def _load_source(self, db, attachment_uid, generation, *, lock=False):
        # This lookup discovers lock keys only; authorization is reloaded after locks.
        hint = db.exec(
            SqlBuilder.select.table(ProjectAttachment).where(
                ProjectAttachment.id == InfraHelper.convert_id(attachment_uid)
            )
        ).first()
        if hint is None:
            return None
        document_hint = self._document(db, hint)
        if document_hint is None:
            return None
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == document_hint.requested_by_id)).first()
        source = self._authorized_source(db, actor, hint.project_id, hint, lock=lock)
        if source is None:
            return None
        board, attachment = source
        if not self._get_service(DoclingMetadataService).detect_document_type(attachment.filename):
            return None
        document = self._document(db, attachment, lock=lock)
        if (
            document is None
            or document.requested_by_id != actor.id
            or (generation is not None and document.generation != generation)
            or document.source_revision != self.source_revision(attachment)
        ):
            return None
        return board, attachment, document

    def load_source(self, attachment_uid, generation=None):
        """Trusted worker lookup. Public transports must use get_readable instead."""
        with DbSession.use(readonly=False) as db:
            return self._load_source(db, attachment_uid, generation)

    def claim(self, attachment_uid, generation):
        with DbSession.atomic() as db:
            source = self._load_source(db, attachment_uid, generation, lock=True)
            if source is None or source[2].status != "pending":
                return None
            source[2].status = "processing"
            db.update(source[2])
            return source

    def publish_extraction(
        self, attachment_uid, generation, *, markdown, keywords, document_json, extraction_cache_token=None
    ):
        if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            return False
        if document_json is not None:
            if not isinstance(document_json, dict):
                return False
            try:
                if len(dumps(document_json, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_DOCUMENT_BYTES:
                    return False
            except (TypeError, ValueError, OverflowError, RecursionError):
                return False
        with DbSession.atomic() as db:
            source = self._load_source(db, attachment_uid, generation, lock=True)
            if source is None or source[2].status != "processing":
                return False
            if extraction_cache_token is not None:
                from ....tasks.docling.DocumentExtractionCache import activate_extraction_reference

                if not activate_extraction_reference(
                    db,
                    extraction_cache_token,
                    source_type=self.source_type,
                    source_uid=source[1].get_uid(),
                    generation=generation,
                ):
                    return False
            if extraction_cache_token is None:
                from ....tasks.docling.DocumentExtractionCache import release_extraction_source

                release_extraction_source(db, self.source_type, source[1].get_uid())
            document = source[2]
            document.text_source = self._published_source(source[1], document)
            document.markdown = markdown
            document.keywords = normalize_keywords(keywords, list(KEYWORD_LANGUAGES))
            document.document_json = document_json
            document.status = "indexed"
            document.error = None
            db.update(document)
            return True

    def publish_embedding(self, attachment_uid, generation, *, expected_embedding, embedding):
        if not isinstance(embedding, dict):
            return False
        with DbSession.atomic() as db:
            source = self._load_source(db, attachment_uid, generation, lock=True)
            if source is None or source[2].status != "indexed" or source[2].embedding != expected_embedding:
                return False
            board, attachment, document = source
            previous = (document.embedding or {}).get("pointer")
            if embedding.get("status") == "indexed":
                pointer = embedding.get("pointer")
                if (
                    not isinstance(pointer, dict)
                    or any(pointer.get(key) != value for key, value in self.vector_source(board, attachment).items())
                    or any(
                        key in pointer
                        for key in ("card_uid", "wiki_uid")
                        if key not in self.vector_source(board, attachment)
                    )
                ):
                    return False
                from ....tasks.docling.DocumentCleanupTask import record_cleanup
                from ....tasks.docling.DocumentEmbeddingCache import activate_cache_references

                storage = pointer.get("storage") or {}
                activate_cache_references(
                    db, storage.get("cache_use_token"), expected_count=storage.get("cache_reference_count")
                )
                document.vector_source = self._published_source(attachment, document)
                if previous != pointer:
                    record_cleanup(db, previous)
            elif previous and embedding.get("pointer") != previous:
                # Pending/failed embedding states retain the last valid published pointer.
                return False
            document.embedding = embedding
            db.update(document)
            return True

    def mark_failed(self, attachment_uid, generation, *, phase, only_pending=False):
        if phase not in {"extraction", "embedding"}:
            return False
        with DbSession.atomic() as db:
            source = self._load_source(db, attachment_uid, generation, lock=True)
            if source is None:
                return False
            document = source[2]
            if phase == "extraction":
                if document.status not in ({"pending"} if only_pending else {"pending", "processing"}):
                    return False
                document.status = "failed"
                document.error = "Document extraction failed; request processing again"
            else:
                # Dispatch failure can arrive after an ambiguously accepted task finishes.
                # Only an unstarted request may be failed without an expected-state CAS.
                if document.status != "indexed" or (
                    (document.embedding or {}).get("status") not in {None, "pending"}
                    and (
                        (document.vector_source or {}).get("generation") == generation
                        or not (document.embedding or {}).get("pointer")
                    )
                ):
                    return False
                document.embedding = {
                    **(document.embedding or {}),
                    "status": "failed",
                    "error": "Document embedding failed; request processing again",
                }
            db.update(document)
            return True

    def _published_source(self, attachment, document):
        return {
            "file": attachment.file.model_dump(mode="json"),
            "filename": attachment.filename,
            "content_sha256": attachment.content_sha256,
            "source_revision": document.source_revision,
            "generation": document.generation,
            "vision_config": deepcopy(document.vision_config),
            "embedding_config": deepcopy(document.embedding_config),
        }

    def _preserve_published(self, attachment, document):
        # Nullable snapshots preserve compatibility with rows predating this lifecycle.
        if document.status == "indexed" and document.text_source is None:
            document.text_source = self._published_source(attachment, document)
        if (document.embedding or {}).get("pointer") and document.vector_source is None:
            document.vector_source = deepcopy(document.text_source) or self._published_source(attachment, document)

    def supersede(self, db, attachment):
        """Fence old workers inside byte replacement, retaining last successful outputs."""
        document = self._document(db, attachment, lock=True)
        if document is not None:
            # Published snapshots are captured before replacement by every publication;
            # migration backfills legacy rows before this method can run.
            document.source_revision = self.source_revision(attachment)
            document.generation = uuid4().hex
            document.status = "pending"
            document.error = None
            db.update(document)

    def invalidate(self, db, attachment, *, preserve_extraction=False):
        """Caller owns the project/attachment locks and source replacement transaction."""
        if not preserve_extraction:
            from ....tasks.docling.DocumentExtractionCache import release_extraction_source

            release_extraction_source(db, self.source_type, attachment.get_uid())
        document = self._document(db, attachment, lock=True)
        if document is not None:
            from ....tasks.docling.DocumentCleanupTask import record_cleanup

            record_cleanup(db, (document.embedding or {}).get("pointer"))
            db.exec(delete(self.document_model).where(self.document_model.id == document.id))

    def record_scope_embedding_cleanup(self, project):
        with DbSession.atomic() as db:
            db.exec(SqlBuilder.select.table(Project).where(Project.id == project.id).with_for_update()).first()
            attachments = db.exec(
                SqlBuilder.select.table(ProjectAttachment, with_deleted=True)
                .where(ProjectAttachment.project_id == project.id)
                .order_by(ProjectAttachment.id)
                .with_for_update()
            ).all()
            for attachment in attachments:
                self.invalidate(db, attachment)
