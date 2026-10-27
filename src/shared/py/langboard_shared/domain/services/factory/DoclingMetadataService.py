import json
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
from sqlalchemy import delete, select, update
from ....core.db import BaseDbModel, DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.routing import SocketTopic
from ....core.types import SafeDateTime
from ....domain.models import Card, CardAttachment, CardDocumentArtifact, CardMetadata, Project
from ....domain.models.bases import BaseMetadataModel
from ....Env import Env
from ....helpers import InfraHelper
from ....publishers import MetadataPublisher
from ....tasks.docling.DocumentKeywords import KEYWORD_LANGUAGES, normalize_keywords
from ..CardChangeTransaction import dispatch_after_commit


DOCLING_DOCUMENTS_METADATA_KEY = "__system.docling_documents"

SUPPORTED_DOCLING_DOCUMENT_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    **{suffix: "image" for suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")},
}


class DoclingIndexStatus(str, Enum):
    Pending = "pending"
    Processing = "processing"
    Disabled = "disabled"
    Indexed = "indexed"
    Failed = "failed"


class DoclingMetadataService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "docling_metadata"

    def get_metadata_map(
        self, model_cls: type[BaseMetadataModel], foreign_key: str, foreign_ids: list[int]
    ) -> dict[int, dict[str, str]]:
        metadata_records = self.repo.metadata.get_by_foreign_ids_and_key(
            model_cls, foreign_key, foreign_ids, DOCLING_DOCUMENTS_METADATA_KEY
        )
        return {getattr(metadata, foreign_key): {metadata.key: metadata.value} for metadata in metadata_records}

    @staticmethod
    def current_embedding_config() -> dict | None:
        """Read the current global binding once from primary, without provider calls."""
        from ....domain.models import InternalBot
        from ....domain.models.InternalBot import InternalBotType
        from ....tasks.docling.DocumentEmbedding import snapshot_embedding_config

        with DbSession.use(readonly=False) as db:
            binding = db.exec(
                SqlBuilder.select.table(InternalBot)
                .where(
                    InternalBot.bot_type == InternalBotType.DocumentEmbedding,
                    InternalBot.is_default == True,  # noqa: E712
                    InternalBot.deleted_at.is_(None),
                )
                .limit(1)
                .execution_options(populate_existing=True)
            ).first()
            if binding is None:
                return None
            try:
                return snapshot_embedding_config(binding.value, binding.get_uid())
            except (ValueError, TypeError):
                return None

    @staticmethod
    def project_api_documents(value: str, current: dict | None) -> str:
        """Project safe freshness without mutating stored snapshots or vector pointers."""
        from ....tasks.docling.DocumentEmbedding import embedding_generation_freshness

        try:
            documents = json.loads(value)
        except (ValueError, TypeError):
            return value
        if not isinstance(documents, list):
            return value
        for document in documents:
            if not isinstance(document, dict):
                continue
            embedding = document.get("embedding")
            if isinstance(embedding, dict):
                # Only the completed generation's snapshot describes its vectors.
                freshness = (
                    embedding_generation_freshness(embedding, current) if embedding.get("status") == "indexed" else None
                )
                document["embedding"] = {key: embedding[key] for key in ("status", "error") if key in embedding}
                if freshness is not None:
                    document["embedding"]["freshness"] = freshness
            document.pop("embedding_config", None)
            document.pop("vision_config", None)
        return json.dumps(documents, ensure_ascii=False, separators=(",", ":"))

    def detect_document_type(self, filename: str) -> str | None:
        return SUPPORTED_DOCLING_DOCUMENT_TYPES.get(Path(filename).suffix.lower())

    def get_content_hash(self, content: bytes) -> str:
        return sha256(content).hexdigest()

    def queue_document(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        filename: str,
        *,
        vision_config: dict[str, Any] | None = None,
        embedding_config: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        with DbSession.atomic() as db:
            db.exec(
                SqlBuilder.select.table(type(foreign_model))
                .where(type(foreign_model).column("id") == foreign_model.id)
                .with_for_update()
            )
            document_type = self.detect_document_type(filename)
            if not document_type:
                return False

            current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid)
            if current and (
                current.get("status") in {DoclingIndexStatus.Pending.value, DoclingIndexStatus.Processing.value}
                or (not force and current.get("status") == DoclingIndexStatus.Indexed.value)
            ):
                return False
            document = {
                **(current or {}),
                "attachment_uid": attachment_uid,
                "document_type": document_type,
                "status": DoclingIndexStatus.Pending.value,
                "generation": uuid4().hex,
                "content": (current or {}).get("content", {}),
                "vision_config": vision_config,
                "embedding_config": embedding_config,
            }
            document.pop("keyword_index_error", None)
            document.pop("location_invalidated", None)
            document.pop("location_reindex_pending", None)
            document.pop("location_reindex_dispatched_at", None)
            document.pop("location_conversion_pending", None)
            if model_cls is CardMetadata:
                from ....tasks.docling.DocumentExtractionCache import (
                    release_document_reservation,
                    reserve_document_references,
                )

                attachment = db.exec(SqlBuilder.select.table(CardAttachment).where(
                    CardAttachment.id == InfraHelper.convert_id(attachment_uid),
                    CardAttachment.card_id == foreign_model.id,
                )).first()
                if attachment is not None:
                    reserve_document_references(
                        db, source_type="card_attachment", source_uid=attachment_uid,
                        generation=document["generation"], content_hash=attachment.content_sha256,
                        filename=filename, vision_config=vision_config, embedding_config=embedding_config,
                        previous_token=current.get("generation")
                        if current and isinstance(current.get("embedding_config"), dict) else None,
                    )
                if current and current.get("generation"):
                    release_document_reservation(
                        db, "card_attachment", attachment_uid, current["generation"],
                        embedding_config=current.get("embedding_config"),
                    )
            from ..KeywordSearchSettings import enabled as keyword_enabled

            if model_cls is CardMetadata and keyword_enabled("index"):
                from ....tasks.docling.DocumentKeywordTask import prepare_index
                from .SecretReferenceService import SecretReferenceUnavailable

                try:
                    document["keyword_index_job_uid"] = prepare_index(
                        db, foreign_model, attachment_uid, document["generation"]
                    )
                except (ValueError, SecretReferenceUnavailable):
                    document["keyword_index_error"] = "keyword_connection_unavailable"
            self.upsert_document(model_cls, foreign_model, document)
            return True

    def claim_document(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        filename: str,
        *,
        generation: str | None = None,
    ) -> bool:
        with DbSession.atomic() as db:
            db.exec(
                SqlBuilder.select.table(type(foreign_model))
                .where(type(foreign_model).column("id") == foreign_model.id)
                .with_for_update()
            )
            current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid) or {}
            if current.get("location_invalidated"):
                return False
            if generation is not None and current.get("generation") != generation:
                return False
            if current.get("status") == DoclingIndexStatus.Indexed.value:
                return False
            if current.get("status") == DoclingIndexStatus.Processing.value:
                started = current.get("started_at")
                try:
                    from datetime import datetime

                    age = (SafeDateTime.now() - datetime.fromisoformat(started)).total_seconds()
                    if age < Env.DOCLING_CONVERSION_TIMEOUT_SECONDS + 30:
                        return False
                except (ValueError, TypeError):
                    return False
            next_generation = uuid4().hex
            if model_cls is CardMetadata and current.get("generation"):
                from ....tasks.docling.DocumentExtractionCache import transfer_document_reservation

                transfer_document_reservation(
                    db, "card_attachment", attachment_uid, current["generation"], next_generation,
                    embedding_config=current.get("embedding_config"),
                )
            self.upsert_document(
                model_cls,
                foreign_model,
                {
                    **current,
                    "attachment_uid": attachment_uid,
                    "started_at": SafeDateTime.now().isoformat(),
                    "generation": next_generation,
                },
            )
            self.mark_document_processing(model_cls, foreign_model, attachment_uid, filename)
            return True

    def mark_document_processing(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        filename: str,
        completed_pages: int = 0,
        total_pages: int | None = None,
        *,
        generation: str | None = None,
    ) -> bool:
        current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid) or {}
        if total_pages is not None and (total_pages < 1 or not 0 <= completed_pages <= total_pages):
            raise ValueError("Invalid document page progress")
        return self.upsert_document(
            model_cls,
            foreign_model,
            {
                **current,
                "attachment_uid": attachment_uid,
                "document_type": self.detect_document_type(filename) or "unknown",
                "status": DoclingIndexStatus.Processing.value,
                "started_at": current.get("started_at") or SafeDateTime.now().isoformat(),
                "completed_pages": completed_pages,
                "total_pages": total_pages,
                # 100% belongs to the committed indexed result, not the last inference request.
                "progress_percent": min(99, completed_pages * 100 // total_pages) if total_pages else None,
                "content": current.get("content", {}),
            },
            expected_generation=generation,
        )

    def mark_document_disabled(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        filename: str,
    ) -> None:
        with DbSession.atomic() as db:
            db.exec(SqlBuilder.select.table(type(foreign_model)).where(
                type(foreign_model).column("id") == foreign_model.id,
            ).with_for_update())
            current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid)
            if model_cls is CardMetadata and current and current.get("generation"):
                from ....tasks.docling.DocumentExtractionCache import release_document_reservation

                release_document_reservation(
                    db, "card_attachment", attachment_uid, current["generation"],
                    embedding_config=current.get("embedding_config"),
                )
            self.upsert_document(
                model_cls,
                foreign_model,
                {
                    "attachment_uid": attachment_uid,
                    "document_type": self.detect_document_type(filename) or "unknown",
                    "status": DoclingIndexStatus.Disabled.value,
                    "content": {},
                },
            )

    def mark_document_indexed(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        document_type: str,
        content_hash: str | None = None,
        content: dict[str, Any] | None = None,
        *,
        generation: str | None = None,
        extraction_cache_token: str | None = None,
    ) -> bool:
        if extraction_cache_token is not None and (model_cls is not CardMetadata or generation is None):
            return False
        content = dict(content or {})
        structural = content.pop("docling_document", None)
        current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid) or {}
        document = {
            **current,
            "attachment_uid": attachment_uid,
            "document_type": document_type,
            "status": DoclingIndexStatus.Indexed.value,
            "progress_percent": 100,
            "content_hash": content_hash,
            "indexed_at": SafeDateTime.now().isoformat(),
            "content": content or {},
        }
        if document.pop("location_conversion_pending", None) and not isinstance(document.get("embedding_config"), dict):
            document.pop("location_reindex_pending", None)
            document.pop("location_reindex_dispatched_at", None)
        with DbSession.atomic() as db:
            if model_cls is CardMetadata:
                project = db.exec(
                    SqlBuilder.select.table(Project)
                    .where(Project.id == foreign_model.project_id, Project.deleted_at.is_(None))
                    .with_for_update()
                ).first()
                if project is None:
                    return False
            db.exec(
                SqlBuilder.select.table(type(foreign_model))
                .where(type(foreign_model).column("id") == foreign_model.id)
                .with_for_update()
            )
            latest = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid)
            if generation is not None and (not latest or latest.get("generation") != generation):
                return False
            if model_cls is CardMetadata:
                card = db.exec(
                    SqlBuilder.select.table(Card)
                    .where(
                        Card.id == foreign_model.id,
                        Card.project_id == foreign_model.project_id,
                        Card.deleted_at.is_(None),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).first()
                attachment_id = InfraHelper.convert_id(attachment_uid)
                attachment = db.exec(
                    SqlBuilder.select.table(CardAttachment)
                    .where(
                        CardAttachment.id == attachment_id,
                        CardAttachment.card_id == foreign_model.id,
                        CardAttachment.deleted_at.is_(None),
                    )
                    .with_for_update()
                ).first()
                if project is None or card is None or attachment is None:
                    return False
                if extraction_cache_token is not None:
                    from ....tasks.docling.DocumentExtractionCache import activate_extraction_reference

                    if (
                        not latest
                        or latest.get("location_invalidated")
                        or not activate_extraction_reference(
                            db,
                            extraction_cache_token,
                            source_type="card_attachment",
                            source_uid=attachment.get_uid(),
                            generation=latest.get("generation"),
                        )
                    ):
                        return False
                    if not isinstance(latest.get("embedding_config"), dict):
                        from ....tasks.docling.DocumentExtractionCache import release_document_reservation

                        release_document_reservation(db, "card_attachment", attachment_uid, generation)
                result = db.exec(
                    update(CardAttachment)
                    .where(CardAttachment.id == attachment_id)
                    .where(CardAttachment.card_id == foreign_model.id)
                    .where(CardAttachment.deleted_at.is_(None))
                    .values(
                        document_text="\n\n".join(
                            filter(
                                None,
                                [
                                    str((content or {}).get("markdown", "")),
                                    " ".join(
                                        word
                                        for words in normalize_keywords(
                                            (content or {}).get("search_keywords"), list(KEYWORD_LANGUAGES), limit=32
                                        ).values()
                                        for word in words
                                    ),
                                ],
                            )
                        )
                    )
                )
                if not result:
                    return False
                artifact = db.exec(
                    SqlBuilder.select.table(CardDocumentArtifact).where(
                        CardDocumentArtifact.attachment_id == attachment_id
                    )
                ).first()
                if structural is not None:
                    if not isinstance(structural, dict) or structural.get("schema_name") != "DoclingDocument":
                        raise ValueError("Invalid structural Docling document")
                    encoded = json.dumps(structural, ensure_ascii=False)
                    if artifact:
                        artifact.document_json = encoded
                        db.update(artifact)
                    else:
                        db.insert(CardDocumentArtifact(attachment_id=attachment_id, document_json=encoded))
                elif artifact:
                    db.delete(artifact)
            self.upsert_document(model_cls, foreign_model, document)
            if model_cls is CardMetadata and document.get("keyword_index_job_uid"):
                from ....tasks.docling.DocumentKeywordTask import activate_index

                activate_index(
                    db, foreign_model, attachment_uid, document.get("generation", ""), document["keyword_index_job_uid"]
                )
        return True

    def mark_document_failed(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        attachment_uid: str,
        filename: str,
        error_message: str,
        *,
        generation: str | None = None,
    ) -> bool:
        document_type = self.detect_document_type(filename) or "unknown"
        current = self.get_document_by_attachment_uid(model_cls, foreign_model, attachment_uid)
        document = {
            **(
                current
                or {
                    "attachment_uid": attachment_uid,
                    "document_type": document_type,
                    "status": DoclingIndexStatus.Pending.value,
                    "content": {},
                }
            ),
            "document_type": document_type,
            "status": DoclingIndexStatus.Failed.value,
            "error_message": error_message,
        }
        return self.upsert_document(model_cls, foreign_model, document, expected_generation=generation)

    def load_documents(self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel) -> list[dict[str, Any]]:
        metadata = self._load_metadata(model_cls, foreign_model)
        return self.parse_documents(metadata)

    def parse_documents(self, metadata: dict[str, str]) -> list[dict[str, Any]]:
        value = metadata.get(DOCLING_DOCUMENTS_METADATA_KEY)
        if not value:
            return []

        try:
            raw_documents = json.loads(value)
        except json.JSONDecodeError:
            return []

        if not isinstance(raw_documents, list):
            return []

        return [document for document in raw_documents if isinstance(document, dict)]

    def save_documents(
        self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel, documents: list[dict[str, Any]]
    ) -> None:
        if documents:
            value = json.dumps(documents, ensure_ascii=False, separators=(",", ":"))
            self.repo.metadata.save(model_cls, foreign_model, DOCLING_DOCUMENTS_METADATA_KEY, value)
            return

        self.repo.metadata.delete_keys(model_cls, foreign_model, DOCLING_DOCUMENTS_METADATA_KEY)

    def get_document_by_attachment_uid(
        self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel, attachment_uid: str
    ) -> dict[str, Any] | None:
        return next(
            (
                document
                for document in self.load_documents(model_cls, foreign_model)
                if document.get("attachment_uid") == attachment_uid
            ),
            None,
        )

    def get_structural_document(
        self, card: BaseDbModel, attachment_uid: str, *, generation: str, content_hash: str | None
    ) -> dict[str, Any] | None:
        """Read private structure only for the current live attachment generation."""
        with DbSession.atomic() as db:
            db.exec(SqlBuilder.select.table(type(card)).where(type(card).column("id") == card.id).with_for_update())
            current = self.get_document_by_attachment_uid(CardMetadata, card, attachment_uid)
            if not current or current.get("generation") != generation or current.get("content_hash") != content_hash:
                return None
            artifact = db.exec(
                SqlBuilder.select.table(CardDocumentArtifact)
                .join(CardAttachment, CardAttachment.id == CardDocumentArtifact.attachment_id)
                .where(CardAttachment.id == InfraHelper.convert_id(attachment_uid))
                .where(CardAttachment.card_id == card.id)
                .where(CardAttachment.deleted_at.is_(None))
            ).first()
            return json.loads(artifact.document_json) if artifact else None

    def upsert_document(
        self,
        model_cls: type[BaseMetadataModel],
        foreign_model: BaseDbModel,
        document: dict[str, Any],
        *,
        expected_generation: str | None = None,
    ) -> bool:
        attachment_uid = document["attachment_uid"]

        changed = False

        def merge(value: str | None) -> str | None:
            nonlocal changed
            documents = self.parse_documents({DOCLING_DOCUMENTS_METADATA_KEY: value or "[]"})
            current = next((entry for entry in documents if entry.get("attachment_uid") == attachment_uid), None)
            if expected_generation is not None and (not current or current.get("generation") != expected_generation):
                return None
            changed = True
            documents = [current for current in documents if current.get("attachment_uid") != attachment_uid]
            documents.append(document)
            documents.sort(key=lambda current: str(current.get("document_type") or ""))
            return json.dumps(documents, ensure_ascii=False, separators=(",", ":"))

        # Serialize the first metadata insert as well as subsequent read-modify-write updates.
        with DbSession.atomic() as db:
            db.exec(
                SqlBuilder.select.table(type(foreign_model))
                .where(type(foreign_model).column("id") == foreign_model.id)
                .with_for_update()
            )
            self.repo.metadata.update_value_by_key(model_cls, foreign_model, DOCLING_DOCUMENTS_METADATA_KEY, merge)
        return changed

    def publish_document_embedding(
        self,
        card,
        attachment_uid: str,
        generation: str,
        content_hash: str,
        embedding: dict,
        *,
        expected_embedding: dict | None = None,
        embedding_config: dict | None = None,
    ) -> bool:
        """Publish a staged vector generation only while its live source still matches."""
        with DbSession.atomic() as db:
            project = db.exec(
                SqlBuilder.select.table(Project).where(Project.id == card.project_id).with_for_update()
            ).first()
            if project is None or project.deleted_at is not None:
                return False
            current_card = db.exec(
                SqlBuilder.select.table(type(card)).where(type(card).column("id") == card.id).with_for_update()
            ).first()
            if not current_card or current_card.is_linked_resource or current_card.project_id != project.id:
                return False
            attachment = db.exec(
                SqlBuilder.select.table(CardAttachment)
                .where(CardAttachment.column("id") == InfraHelper.convert_id(attachment_uid))
                .where(CardAttachment.column("card_id") == card.id)
                .where(CardAttachment.column("deleted_at").is_(None))
                .with_for_update()
            ).first()
            if not attachment:
                return False
            document = self.get_document_by_attachment_uid(CardMetadata, card, attachment_uid)
            if (
                not document
                or document.get("generation") != generation
                or document.get("content_hash") != content_hash
                or document.get("status") != "indexed"
                or (expected_embedding is not None and (document.get("embedding") or {}) != expected_embedding)
            ):
                return False
            updated = {**document, "embedding": embedding}
            if embedding.get("status") == "indexed":
                updated.pop("location_reindex_pending", None)
                updated.pop("location_reindex_dispatched_at", None)
                updated.pop("location_invalidated", None)
            if embedding_config is not None:
                updated["embedding_config"] = embedding_config
            published = self.upsert_document(CardMetadata, card, updated, expected_generation=generation)
            if published and embedding.get("status") == "indexed":
                from ....tasks.docling.DocumentEmbeddingCache import activate_cache_references
                from ....tasks.docling.DocumentExtractionCache import release_document_reservation

                storage = (embedding.get("pointer") or {}).get("storage") or {}
                release_document_reservation(db, "card_attachment", attachment_uid, generation)
                activate_cache_references(
                    db, storage.get("cache_use_token"), expected_count=storage.get("cache_reference_count"),
                    reservation_token=generation,
                )
            previous = (document.get("embedding") or {}).get("pointer")
            if published and embedding.get("status") == "indexed" and previous != embedding.get("pointer"):
                from ....tasks.docling.DocumentCleanupTask import record_cleanup

                record_cleanup(db, previous)
            return published

    def delete_document_by_attachment_uid(
        self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel, attachment_uid: str
    ) -> dict[str, Any] | None:
        with DbSession.atomic() as db:
            db.exec(
                SqlBuilder.select.table(type(foreign_model))
                .where(type(foreign_model).column("id") == foreign_model.id)
                .with_for_update()
            )
            current = self.load_documents(model_cls, foreign_model)
            removed = next((document for document in current if document.get("attachment_uid") == attachment_uid), None)
            documents = [document for document in current if document.get("attachment_uid") != attachment_uid]
            self.save_documents(model_cls, foreign_model, documents)
            if model_cls is CardMetadata:
                # Metadata may already be missing. Clear the selected derived
                # search projection without deleting or rewriting its attachment.
                attachment_scope = (
                    CardAttachment.id == InfraHelper.convert_id(attachment_uid),
                    CardAttachment.card_id == foreign_model.id,
                )
                attachment = db.exec(SqlBuilder.select.table(CardAttachment).where(*attachment_scope)).first()
                if attachment is not None:
                    from ....tasks.docling.DocumentExtractionCache import (
                        release_document_reservation,
                        release_extraction_source,
                    )

                    if removed and removed.get("generation"):
                        release_document_reservation(
                            db, "card_attachment", attachment.get_uid(), removed["generation"],
                            embedding_config=removed.get("embedding_config"),
                        )
                    release_extraction_source(db, "card_attachment", attachment.get_uid())
                db.exec(update(CardAttachment).where(*attachment_scope).values(document_text=""))
                db.exec(
                    delete(CardDocumentArtifact).where(
                        CardDocumentArtifact.attachment_id.in_(select(CardAttachment.id).where(*attachment_scope))
                    )
                )
                from ....tasks.docling.DocumentKeywordTask import record_removal

                record_removal(db, foreign_model, attachment_uid)
            if model_cls is CardMetadata and removed:
                from ....tasks.docling.DocumentCleanupTask import record_cleanup

                record_cleanup(db, (removed.get("embedding") or {}).get("pointer"))
            return removed

    def invalidate_card_location(self, project: Project, card: Card) -> None:
        """Fence old-board work and invalidate its derived search locations.

        Converted content and attachment provenance survive. Location invalidation
        never schedules another conversion or model inference.
        """
        from ....domain.models.DocumentExtractionCache import DocumentExtractionCacheReference
        from ....tasks.docling.DocumentKeywordTask import record_scope_removal

        with DbSession.atomic() as db:
            current_project = db.exec(
                SqlBuilder.select.table(Project)
                .where(Project.id == project.id, Project.deleted_at.is_(None))
                .with_for_update()
            ).first()
            current = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.id == card.id, Card.project_id == project.id, Card.deleted_at.is_(None))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if current_project is None or current is None:
                raise ValueError("Card source location changed")
            record_scope_removal(db, project, current)
            documents = self.load_documents(CardMetadata, current)
            for document in documents:
                previous_generation = document.get("generation")
                document["generation"] = uuid4().hex
                if previous_generation is not None:
                    from ....tasks.docling.DocumentExtractionCache import transfer_document_reservation

                    transfer_document_reservation(
                        db, "card_attachment", document["attachment_uid"], previous_generation, document["generation"],
                        embedding_config=document.get("embedding_config"),
                    )
                if document.get("status") == DoclingIndexStatus.Indexed.value and previous_generation is not None:
                    # A location fence preserves the exact published conversion lineage.
                    db.exec(update(DocumentExtractionCacheReference).where(
                        DocumentExtractionCacheReference.source_type == "card_attachment",
                        DocumentExtractionCacheReference.source_uid == document["attachment_uid"],
                        DocumentExtractionCacheReference.generation == previous_generation,
                        DocumentExtractionCacheReference.expires_at.is_(None),
                    ).values(generation=document["generation"]))
                # Retain indexed text for native search; pending work is explicitly
                # invalidated, preventing legacy no-generation tasks from restarting it.
                if document.get("status") in {
                    DoclingIndexStatus.Pending.value,
                    DoclingIndexStatus.Processing.value,
                } and isinstance(document.get("vision_config"), dict):
                    document["status"] = DoclingIndexStatus.Pending.value
                    document["location_conversion_pending"] = True
                    document["location_reindex_pending"] = True
                    document.pop("started_at", None)
                elif document.get("status") != DoclingIndexStatus.Indexed.value:
                    document["status"] = DoclingIndexStatus.Disabled.value
                document["location_invalidated"] = True
                old = document.get("embedding") or {}
                # Keep the old pointer and its active cache reference until a
                # replacement publishes. Readers reject its old board/generation.
                document["embedding"] = {**old, "status": "pending", "reason": "card_moved"}
                document.pop("keyword_index_job_uid", None)
                document.pop("keyword_index_error", None)
            self.save_documents(CardMetadata, current, documents)

    def relocate_card_documents(self, card: Card) -> None:
        """Reuse existing transcription and cached vectors at the committed location."""
        from types import SimpleNamespace
        from ....tasks.docling.DoclingMetadataTask import _queue_embedding
        from ....tasks.docling.DocumentKeywordTask import activate_index, prepare_index
        from ..KeywordSearchSettings import enabled as keyword_enabled
        from .SecretReferenceService import SecretReferenceUnavailable

        with DbSession.atomic() as db:
            documents = self.load_documents(CardMetadata, card)
            for document in documents:
                conversion = document.get("location_conversion_pending")
                if not conversion and document.get("status") != DoclingIndexStatus.Indexed.value:
                    continue
                attachment_uid, generation = document["attachment_uid"], document["generation"]
                if keyword_enabled("index"):
                    try:
                        job_uid = prepare_index(db, card, attachment_uid, generation)
                        document["keyword_index_job_uid"] = job_uid
                        if not conversion:
                            activate_index(db, card, attachment_uid, generation, job_uid)
                    except (ValueError, SecretReferenceUnavailable):
                        document["keyword_index_error"] = "keyword_connection_unavailable"
                if conversion:
                    document.pop("location_invalidated", None)
                    db.after_commit(lambda entry=dict(document): self._queue_card_location_conversion(entry))
                elif isinstance(document.get("embedding_config"), dict):
                    document["location_reindex_pending"] = True
                    db.after_commit(
                        lambda uid=attachment_uid, version=generation: _queue_embedding(
                            SimpleNamespace(docling_metadata=self), card, uid, version
                        )
                    )
            self.save_documents(CardMetadata, card, documents)

    @staticmethod
    def _queue_card_location_conversion(document):
        """Deliver the existing explicit conversion request at its current generation."""
        from ....core.broker import Broker
        from ....core.broker.TaskParameters import TaskParameters
        from .CardAttachmentService import DOCLING_INDEX_CARD_ATTACHMENT_TASK

        args, kwargs = TaskParameters(
            json.dumps({"attachment_uid": document["attachment_uid"], "generation": document["generation"]})
        ).pack()
        Broker.celery.send_task(DOCLING_INDEX_CARD_ATTACHMENT_TASK, args=args, kwargs=kwargs)

    def recover_card_location_embeddings(self, *, limit: int = 100) -> int:
        """Recover only requested location publications, bounded by both rows and jobs."""
        from types import SimpleNamespace
        from ....tasks.docling.DoclingMetadataTask import _queue_embedding

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Location recovery limit must be between 1 and 100")
        dispatched = 0
        with DbSession.atomic() as db:
            rows = db.exec(
                SqlBuilder.select.tables(CardMetadata, Card)
                .join(Card, Card.id == CardMetadata.card_id)
                .join(Project, Project.id == Card.project_id)
                .where(
                    CardMetadata.key == DOCLING_DOCUMENTS_METADATA_KEY,
                    CardMetadata.value.contains('"location_reindex_pending":true'),
                    Card.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                )
                .order_by(CardMetadata.updated_at, CardMetadata.id)
                .limit(limit)
                .execution_options(populate_existing=True)
            ).all()
            for metadata, card in rows:
                documents = self.parse_documents({DOCLING_DOCUMENTS_METADATA_KEY: metadata.value})
                for document in sorted(documents, key=lambda entry: entry.get("location_reindex_dispatched_at", "")):
                    conversion = (
                        document.get("location_conversion_pending")
                        and isinstance(document.get("vision_config"), dict)
                        and document.get("status") in {"pending", "processing", "failed"}
                    )
                    embedding = document.get("status") == "indexed" and isinstance(
                        document.get("embedding_config"), dict
                    )
                    if (
                        not document.get("location_reindex_pending")
                        or not (conversion or embedding)
                        or not isinstance(document.get("generation"), str)
                    ):
                        continue
                    attachment_uid, generation = document.get("attachment_uid"), document["generation"]
                    if not isinstance(attachment_uid, str):
                        continue
                    if conversion:
                        db.after_commit(lambda entry=dict(document): self._queue_card_location_conversion(entry))
                    else:
                        db.after_commit(
                            lambda card=card, uid=attachment_uid, version=generation: _queue_embedding(
                                SimpleNamespace(docling_metadata=self), card, uid, version
                            )
                        )
                    document["location_reindex_dispatched_at"] = SafeDateTime.now().isoformat()
                    dispatched += 1
                    if dispatched == limit:
                        break
                # Touch only the requested metadata row so a failed or long-running
                # first batch cannot starve later requested moves on the next run.
                db.exec(
                    update(CardMetadata)
                    .where(CardMetadata.id == metadata.id, CardMetadata.value == metadata.value)
                    .values(
                        value=json.dumps(documents, ensure_ascii=False, separators=(",", ":")),
                        updated_at=SafeDateTime.now(),
                    )
                )
                if dispatched == limit:
                    break
        return dispatched

    def record_scope_embedding_cleanup(self, project: Project, card: Card | None = None) -> None:
        """Record derived-index deletion inside the caller's source deletion transaction."""
        from ....tasks.docling.DocumentCleanupTask import record_cleanup

        with DbSession.atomic() as db:
            # Match embedding publication's project -> card lock order. A late
            # inference result must not publish after this source is deleted.
            current_project = db.exec(
                SqlBuilder.select.column(Project.id)
                .where(Project.id == project.id, Project.deleted_at.is_(None))
                .with_for_update()
            ).first()
            if current_project is None:
                return
            if card is not None:
                current_card = db.exec(
                    SqlBuilder.select.column(Card.id)
                    .where(Card.id == card.id, Card.project_id == project.id, Card.deleted_at.is_(None))
                    .with_for_update()
                ).first()
                if current_card is None:
                    return
            from ....tasks.docling.DocumentKeywordTask import record_scope_removal

            record_scope_removal(db, project, card)
            cards = select(Card.id).where(Card.project_id == project.id)
            if card is not None:
                cards = cards.where(Card.id == card.id)
            from ....tasks.docling.DocumentExtractionCache import release_extraction_source

            after_id = 0
            while True:
                rows = db.exec(
                    SqlBuilder.select.columns(CardMetadata.id, CardMetadata.value)
                    .where(
                        CardMetadata.card_id.in_(cards),
                        CardMetadata.key == DOCLING_DOCUMENTS_METADATA_KEY,
                        CardMetadata.id > after_id,
                    )
                    .order_by(CardMetadata.id)
                    .limit(100)
                ).all()
                for _, value in rows:
                    for document in self.parse_documents({DOCLING_DOCUMENTS_METADATA_KEY: value}):
                        if document.get("generation"):
                            from ....tasks.docling.DocumentExtractionCache import release_document_reservation

                            release_document_reservation(
                                db, "card_attachment", document["attachment_uid"], document["generation"],
                                embedding_config=document.get("embedding_config"),
                            )
                        # Bulk deletion uses the existing bounded recovery cron,
                        # avoiding one synchronous broker call per document.
                        record_cleanup(db, (document.get("embedding") or {}).get("pointer"), dispatch=card is not None)
                if len(rows) < 100:
                    break
                after_id = rows[-1][0]
            attachment_after_id = 0
            while True:
                attachments = db.exec(
                    SqlBuilder.select.table(CardAttachment, with_deleted=True)
                    .where(CardAttachment.card_id.in_(cards), CardAttachment.id > attachment_after_id)
                    .order_by(CardAttachment.id)
                    .limit(100)
                ).all()
                for attachment in attachments:
                    release_extraction_source(db, "card_attachment", attachment.get_uid())
                if len(attachments) < 100:
                    break
                attachment_after_id = attachments[-1].id

    def publish_update(
        self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel, topic: SocketTopic
    ) -> None:
        # Publish the committed state, never a lagging replica's prior progress.
        metadata = self.repo.metadata.get_by_key(
            model_cls, foreign_model, DOCLING_DOCUMENTS_METADATA_KEY, readonly=False
        )
        topic_uid = foreign_model.get_uid()
        if metadata:
            dispatch_after_commit(
                MetadataPublisher.updated_metadata, topic, topic_uid, DOCLING_DOCUMENTS_METADATA_KEY, metadata.value
            )
            return

        dispatch_after_commit(MetadataPublisher.deleted_metadata, topic, topic_uid, [DOCLING_DOCUMENTS_METADATA_KEY])

    def _load_metadata(self, model_cls: type[BaseMetadataModel], foreign_model: BaseDbModel) -> dict[str, str]:
        # Claims and generation fences need read-after-write consistency too.
        metadata = self.repo.metadata.get_by_key(
            model_cls, foreign_model, DOCLING_DOCUMENTS_METADATA_KEY, readonly=False
        )
        return {metadata.key: metadata.value} if metadata else {}
