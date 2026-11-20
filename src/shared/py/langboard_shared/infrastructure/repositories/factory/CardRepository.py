from hashlib import sha256
from typing import Any, Sequence
from sqlalchemy import (
    JSON,
    BigInteger,
    Text,
    case,
    cast,
    false,
    func,
    literal_column,
    or_,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import defer, load_only
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseOrderRepository
from ....core.schema import TimeBasedPagination
from ....core.types import SafeDateTime, SnowflakeID
from ....core.types.ParamTypes import TCardParam, TColumnParam, TProjectParam, TUserParam
from ....domain.models import (
    Bot,
    Card,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardMetadata,
    Project,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowStageDefinition,
)
from ....domain.services.CardVisibilityPolicy import (
    CardVisibilityContext,
    card_visibility_scope,
    card_visibility_scope_by_project,
)
from ....helpers import InfraHelper


def _editor_search_text(column, dialect: str):
    """Search editor content, excluding model keys and JSON escape syntax."""
    if dialect == "postgresql":
        encoded = cast(column, JSON).op("#>>")(cast([], ARRAY(Text)))
        value = cast(encoded, JSON)
        # PostgreSQL text cannot contain NUL. Retain the prior encoded-text
        # matching semantics for legacy values before any JSON unescaping.
        raw = cast(column, Text)
        return case((raw.contains(r"\u0000", autoescape=True), raw), else_=value.op("->>")("content"))
    if dialect == "sqlite":
        content = func.json_extract(func.json_extract(column, "$"), "$.content")
        # SQLite LIKE stops at NUL; JSON encoding preserves suffix searches.
        return case((func.instr(content, "\x00") > 0, func.json_quote(content)), else_=content)
    if dialect in {"mysql", "mariadb"}:
        return func.json_unquote(func.json_extract(func.json_unquote(column), "$.content"))
    raise ValueError("Unsupported database dialect for editor content search")



class CardRepository(BaseOrderRepository[Card, ProjectColumn]):
    @staticmethod
    def parent_model_cls():
        return ProjectColumn

    @staticmethod
    def model_cls():
        return Card

    @staticmethod
    def name() -> str:
        return "card"

    def get_by_id_like(self, card: TCardParam | None) -> Card | None:
        return InfraHelper.get_by_id_like(Card, card)

    def get_existing_uids(
        self, project: TProjectParam, card_uids: Sequence[str], *, context: CardVisibilityContext
    ) -> list[str]:
        if not card_uids:
            return []
        if len(card_uids) > 200:
            raise ValueError("At most 200 recent cards may be checked")
        project_uid = InfraHelper.convert_uid(InfraHelper.convert_id(project))
        return [
            uid
            for _, uid in self.get_existing_pairs(
                [(project_uid, uid) for uid in card_uids],
                contexts={int(InfraHelper.convert_id(project)): context},
            )
        ]

    def get_existing_pairs(
        self, pairs: Sequence[tuple[str, str]], *, contexts: dict[int, CardVisibilityContext]
    ) -> list[tuple[str, str]]:
        if len(pairs) > 200:
            raise ValueError("At most 200 recent cards may be checked")
        requested = {
            (InfraHelper.convert_id(project_uid), InfraHelper.convert_id(card_uid))
            for project_uid, card_uid in pairs
            if int(InfraHelper.convert_id(project_uid)) in contexts
        }
        if not requested:
            return []
        # One bounded projection; no content, unread state or member metadata loaded.
        with DbSession.use(readonly=False) as db:
            rows = db.exec(
                SqlBuilder.select.columns(Card.project_id, Card.id)
                .join(Project, Project.id == Card.project_id)
                .where(Project.deleted_at.is_(None), Card.archived_at.is_(None))
                .where(
                    select(ProjectColumn.id)
                    .where(
                        ProjectColumn.id == Card.project_column_id,
                        ProjectColumn.project_id == Card.project_id,
                        ProjectColumn.deleted_at.is_(None),
                    )
                    .exists()
                )
                .where(tuple_(Card.project_id, Card.id).in_(requested))
                .where(card_visibility_scope_by_project(contexts))
                .limit(200)
            ).all()
        return [(InfraHelper.convert_uid(project_id), InfraHelper.convert_uid(card_id)) for project_id, card_id in rows]

    def update_description_if_current(self, card: Card, expected_content: str) -> bool:
        """Lock and compare the primary row before updating only its description."""

        with DbSession.use(readonly=False) as db:
            current = db.exec(
                SqlBuilder.select.column(Card.description)
                .where((Card.column("id") == card.id) & (Card.column("project_id") == card.project_id))
                .with_for_update()
            ).first()
            if current is None:
                return False
            content = current.content if current is not None else ""
            if content != expected_content:
                return False
            updated_at = SafeDateTime.now()
            changed = db.exec(
                update(Card.__table__)
                .where(Card.column("id") == card.id)
                .values(
                    description=card.description,
                    updated_at=updated_at,
                )
            )
            if changed != 1:
                return False
        card.updated_at = updated_at
        card.clear_changes()
        return True

    def find_linked_resource(self, project: TProjectParam, source_type: str, source_uid: str) -> Card | None:
        project_id = InfraHelper.convert_id(project)
        with DbSession.use(readonly=True) as db:
            return db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.column("project_id") == project_id)
                .where(Card.column("source_type") == source_type)
                .where(Card.column("source_uid") == source_uid)
                .limit(1)
            ).first()

    def get_linked_resource_map(
        self,
        project: TProjectParam,
        source_type: str,
        source_uids: Sequence[str],
    ) -> dict[str, Card]:
        if not source_uids:
            return {}
        project_id = InfraHelper.convert_id(project)
        with DbSession.use(readonly=True) as db:
            cards = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.column("project_id") == project_id)
                .where(Card.column("source_type") == source_type)
                .where(Card.column("source_uid").in_(set(source_uids)))
            ).all()
        return {card.source_uid: card for card in cards if card.source_uid is not None}

    def get_board_list(
        self, project: TProjectParam, archive_visible_since: SafeDateTime, *, context: CardVisibilityContext
    ) -> list[tuple[Card, int]]:
        project_id = InfraHelper.convert_id(project)
        hot_scope = (Card.column("archived_at") == None) | (Card.column("archived_at") >= archive_visible_since)  # noqa: E711
        comment_counts = (
            SqlBuilder.select.columns(
                CardComment.column("card_id"),
                func.count(CardComment.column("id")).label("count_comment"),  # type: ignore
            )
            .join(Card, Card.column("id") == CardComment.column("card_id"))
            .where(Card.column("project_id") == project_id)
            .where(Card.deleted_at.is_(None), card_visibility_scope(context))
            .where(hot_scope)
            .group_by(CardComment.column("card_id"))
            .subquery()
        )

        cards = []
        with DbSession.use(readonly=False) as db:
            result = db.exec(
                SqlBuilder.select.tables(
                    Card,
                    func.coalesce(comment_counts.c.count_comment, 0).label("count_comment"),  # pyright: ignore[reportArgumentType]
                )
                .options(defer(Card.ai_description, raiseload=True))
                .outerjoin(comment_counts, Card.column("id") == comment_counts.c.card_id)
                .where(Card.column("project_id") == project_id)
                .where(Card.deleted_at.is_(None), card_visibility_scope(context))
                .where(hot_scope)
                .order_by(Card.column("order").asc())
            )
            cards = result.all()

        return cards

    def get_board_creators(
        self,
        project: TProjectParam,
        archive_visible_since: SafeDateTime,
        *,
        context: CardVisibilityContext | None = None,
    ) -> dict[int, User | Bot]:
        """Resolve immutable card authors with one query instead of per-card lookups."""

        project_id = InfraHelper.convert_id(project)
        query = (
            SqlBuilder.select.tables(Card.column("id"), User, Bot)
            .options(
                load_only(User.id, User.firstname, User.lastname, User.avatar, User.deleted_at, raiseload=True),
                load_only(Bot.id, Bot.name, Bot.avatar, Bot.deleted_at, raiseload=True),
            )
            .select_from(Card)
            .outerjoin(User, Card.column("created_by_user_id") == User.column("id"))
            .outerjoin(Bot, Card.column("created_by_bot_id") == Bot.column("id"))
            .where(Card.column("project_id") == project_id)
            .where(Card.column("deleted_at").is_(None))
            .where(
                (Card.column("archived_at") == None)  # noqa: E711
                | (Card.column("archived_at") >= archive_visible_since)
            )
        )

        if context is not None:
            query = query.where(card_visibility_scope(context))

        creators: dict[int, User | Bot] = {}
        with DbSession.use(readonly=True) as db:
            for card_id, user, bot in db.exec(query).all():
                creator = user if user is not None else bot
                if creator is not None:
                    creators[card_id] = creator
        return creators

    def get_archived_page_by_project(
        self,
        project: TProjectParam,
        limit: int,
        before_archived_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        input_value: str | None = None,
        *,
        context: CardVisibilityContext,
    ) -> list[tuple[Card, ProjectColumn]]:
        """Return a bounded archived-card keyset page without entering the hot board query."""

        project_id = InfraHelper.convert_id(project)
        query = (
            SqlBuilder.select.tables(Card, ProjectColumn)
            .join(
                ProjectColumn,
                (Card.column("project_column_id") == ProjectColumn.column("id"))
                & (ProjectColumn.column("project_id") == project_id),
            )
            .where(Card.column("project_id") == project_id)
            .where(Card.column("archived_at") != None)  # noqa: E711
            .where(Card.deleted_at.is_(None), card_visibility_scope(context))
        )
        if input_value:
            escaped_input = input_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Card.column("title").ilike(f"%{escaped_input}%", escape="\\"))
        if before_archived_at is not None:
            if before_card is None:
                raise ValueError("before_card is required with before_archived_at")
            before_card_id = InfraHelper.convert_id(before_card)
            query = query.where(
                (Card.column("archived_at") < before_archived_at)
                | ((Card.column("archived_at") == before_archived_at) & (Card.column("id") < before_card_id))
            )
        query = query.order_by(Card.column("archived_at").desc(), Card.column("id").desc()).limit(limit + 1)
        with DbSession.use(readonly=True) as db:
            return list(db.exec(query).all())

    def count_archived_by_project(
        self, project: TProjectParam, input_value: str | None = None, *, context: CardVisibilityContext
    ) -> int:
        """Count archived project cards, optionally applying the archive title search."""

        project_id = InfraHelper.convert_id(project)
        query = (
            SqlBuilder.select.count(Card, Card.column("id"))
            .where(Card.column("project_id") == project_id)
            .where(Card.column("archived_at") != None)  # noqa: E711
            .where(Card.deleted_at.is_(None), card_visibility_scope(context))
        )
        if input_value:
            escaped_input = input_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Card.column("title").ilike(f"%{escaped_input}%", escape="\\"))
        with DbSession.use(readonly=True) as db:
            return db.exec(query).first() or 0

    def get_dashboard_list_scroller(
        self,
        user: TUserParam,
        pagination: TimeBasedPagination,
        *,
        contexts: dict[int, CardVisibilityContext],
    ):
        if not contexts:
            return []
        user_id = InfraHelper.convert_id(user)
        query = (
            SqlBuilder.select.tables(Card, Project, ProjectColumn)
            .join(Project, Card.column("project_id") == Project.column("id"))
            .join(
                ProjectColumn,
                (Card.column("project_column_id") == ProjectColumn.column("id"))
                & (ProjectColumn.column("project_id") == Project.column("id")),
            )
            .join(ProjectRole, Project.column("id") == ProjectRole.column("project_id"))
            .outerjoin(
                CardAssignedUser,
                Card.column("id") == CardAssignedUser.column("card_id"),
            )
            .where(
                (ProjectRole.column("user_id") == user_id)
                & (
                    (
                        (ProjectRole.column("actions") == "*")
                        & (
                            (CardAssignedUser.column("card_id") == None)  # noqa
                            | (CardAssignedUser.column("user_id") == user_id)
                        )
                    )
                    | ((ProjectRole.column("actions") != "*") & (CardAssignedUser.column("user_id") == user_id))
                )
            )
            .where(card_visibility_scope_by_project(contexts))
            .where(Card.column("created_at") <= pagination.refer_time)
            .order_by(Card.column("created_at").desc(), Card.column("id").desc())
        )
        query = InfraHelper.paginate(query, pagination.page, pagination.limit)

        records = []
        with DbSession.use(readonly=False) as db:
            result = db.exec(query)
            records = result.all()
        return records

    def get_all_by_project(self, project: TProjectParam, *, context: CardVisibilityContext | None = None):
        project_id = InfraHelper.convert_id(project)

        records = []
        with DbSession.use(readonly=context is None) as db:
            result = db.exec(
                SqlBuilder.select.tables(Card, ProjectColumn)
                .join(
                    ProjectColumn,
                    Card.column("project_column_id") == ProjectColumn.column("id"),
                )
                .where(Card.column("project_id") == project_id)
                .where(card_visibility_scope(context) if context is not None else True)
                .where(ProjectColumn.project_id == project_id if context is not None else True)
                .where(Card.deleted_at.is_(None))
                .order_by(Card.column("order").asc())
            )
            records = result.all()
        return records

    def search_context_by_project(
        self,
        project: TProjectParam | None,
        input_value: str,
        limit: int = 20,
        date_field: str = "updated_at",
        since: SafeDateTime | None = None,
        until: SafeDateTime | None = None,
        *,
        context: CardVisibilityContext,
        include_closed: bool = True,
        workflow_stages: list[str] | None = None,
        visibility: list[str] | None = None,
        created_by: list[str] | None = None,
        assigned_to: list[str] | None = None,
        additional_card_ids: Sequence[int] = (),
        before_updated_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        paginated: bool = False,
        contexts: dict[int, CardVisibilityContext] | None = None,
    ) -> list[tuple[Card, ProjectColumn]] | tuple[list[tuple[Card, ProjectColumn]], int]:
        project_id = InfraHelper.convert_id(project) if project is not None else None
        escaped_input = input_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        title = Card.column("title")
        pattern = f"%{escaped_input}%"
        dialect = DbEngine.get_main_engine().dialect.name
        comments = (
            select(CardComment.column("id"))
            .where(CardComment.column("card_id") == Card.column("id"))
            .where(CardComment.column("deleted_at") == None)  # noqa: E711
            .where(_editor_search_text(CardComment.column("content"), dialect).ilike(pattern, escape="\\"))
            .exists()
        )
        attachments = (
            select(CardAttachment.column("id"))
            .where(CardAttachment.column("card_id") == Card.column("id"))
            .where(CardAttachment.column("deleted_at") == None)  # noqa: E711
            .where(
                or_(
                    CardAttachment.column("filename").ilike(pattern, escape="\\"),
                    (CardAttachment.column("document_text") != "")
                    & CardAttachment.column("document_text").ilike(pattern, escape="\\"),
                )
            )
            .exists()
        )
        query = (
            SqlBuilder.select.tables(Card, ProjectColumn)
            .join(
                ProjectColumn,
                (Card.column("project_column_id") == ProjectColumn.column("id"))
                & (ProjectColumn.column("project_id") == Card.project_id),
            )
            .where(Card.project_id == project_id if project_id is not None else true())
            .where(
                card_visibility_scope_by_project(contexts) if contexts is not None else card_visibility_scope(context)
            )
            .where(Card.column("source_type") == None)  # noqa: E711
            .where(
                or_(
                    # Each of the three providers contributes at most 20 validated cards per board.
                    # Apply the global page limit after merging the bounded candidates.
                    Card.id.in_(additional_card_ids if contexts is not None else additional_card_ids[:60])
                    if additional_card_ids else false(),
                    title.ilike(pattern, escape="\\"),
                    _editor_search_text(Card.column("description"), dialect).ilike(pattern, escape="\\"),
                    comments,
                    attachments,
                )
            )
        )
        query = self._filter_project_workflow(query, include_closed, workflow_stages)
        query = self._filter_card_groups(query, context, visibility, created_by, assigned_to)
        if date_field not in {"created_at", "updated_at"}:
            raise ValueError("date_field must be created_at or updated_at")
        if since is not None and until is not None and since >= until:
            raise ValueError("since must be earlier than until")
        date_column = Card.column(date_field)
        if since is not None:
            query = query.where(date_column >= since)
        if until is not None:
            query = query.where(date_column < until)
        count_query = select(func.count()).select_from(query.subquery())
        if before_updated_at is not None:
            query = query.where(
                (Card.updated_at < before_updated_at)
                | ((Card.updated_at == before_updated_at) & (Card.id < InfraHelper.convert_id(before_card)))
            )
        query = query.order_by(Card.updated_at.desc(), Card.id.desc()).limit(limit + int(paginated))
        with DbSession.use(readonly=False) as db:
            records = list(db.exec(query).all())
            return (records, db.exec(count_query).first()[0]) if paginated else records

    @staticmethod
    def _published_filenames(db, hits):
        """Transfer only bounded published labels for an already authorized hit set."""
        if not hits:
            return {}
        identifiers = [row[0].to_short_code() for row in hits]
        dialect = DbEngine.get_main_engine().dialect.name
        if dialect == "sqlite":
            entries = func.json_each(CardMetadata.value).table_valued("key", "value").alias("published_sources")
            uid = func.json_extract(entries.c.value, "$.attachment_uid")
            filename = func.json_extract(entries.c.value, "$.text_source.filename")
        elif dialect == "postgresql":
            entries = func.jsonb_array_elements(cast(CardMetadata.value, JSONB)).table_valued("value").alias("published_sources")
            uid = entries.c.value.op("->>")("attachment_uid")
            filename = entries.c.value.op("->")("text_source").op("->>")("filename")
        elif dialect in {"mysql", "mariadb"}:
            entries = func.JSON_TABLE(
                CardMetadata.value,
                literal_column("'$[*]' COLUMNS (attachment_uid VARCHAR(128) PATH '$.attachment_uid', "
                               "filename VARCHAR(1024) PATH '$.text_source.filename')"),
            ).table_valued("attachment_uid", "filename").alias("published_sources")
            uid, filename = entries.c.attachment_uid, entries.c.filename
        else:
            raise ValueError("Unsupported database dialect for document source labels")
        labels = db.exec(
            select(CardMetadata.card_id, uid, func.substr(filename, 1, 1024))
            .select_from(CardMetadata).join(entries, true())
            .where(CardMetadata.card_id.in_({row[1] for row in hits}),
                   CardMetadata.key == "__system.docling_documents", uid.in_(identifiers), filename.is_not(None))
            .limit(len(hits))
        ).all()
        return {(card_id, attachment_uid): filename for card_id, attachment_uid, filename in labels}

    def validate_document_candidates(
        self, project: TProjectParam, candidates: list[dict[str, str]], *, context: CardVisibilityContext
    ) -> dict[int, list[dict[str, str]]]:
        """Materialize bounded external candidates only from current authorized sources."""
        sources: dict[int, list[dict[str, str]]] = {}
        for source in candidates[:20]:
            try:
                attachment_id = InfraHelper.convert_id(source["attachment_uid"])
                if 0 < attachment_id <= SnowflakeID.MAX_VALUE:
                    sources.setdefault(attachment_id, []).append(source)
            except (ValueError, TypeError, KeyError):
                continue
        if not sources:
            return {}
        statement = (
            select(CardAttachment.id, Card.id, CardAttachment.filename, CardAttachment.document_text)
            .join(Card, Card.id == CardAttachment.card_id)
            .join(
                ProjectColumn,
                (ProjectColumn.id == Card.project_column_id) & (ProjectColumn.project_id == Card.project_id),
            )
            .where(Card.project_id == InfraHelper.convert_id(project), card_visibility_scope(context))
            .where(Card.deleted_at.is_(None), ProjectColumn.deleted_at.is_(None), Card.source_type.is_(None))
            .where(CardAttachment.id.in_(sources), CardAttachment.deleted_at.is_(None))
            .where(CardAttachment.document_text != "", func.length(CardAttachment.document_text) <= 131072)
            .order_by(CardAttachment.id)
            .limit(20)
        )
        result: dict[int, list[dict[str, str]]] = {}
        with DbSession.use(readonly=False) as db:
            hits = db.exec(statement).all()
            labels = self._published_filenames(db, hits)
            for attachment_id, card_id, filename, body in hits:
                fingerprint = sha256(body.encode("utf-8")).hexdigest()
                if not any(
                    source.get("card_uid") == card_id.to_short_code() and source.get("projection_hash") == fingerprint
                    for source in sources[attachment_id]
                ):
                    continue
                matches = result.setdefault(card_id, [])
                if len(matches) < 2:
                    matches.append(
                        {"attachment_uid": attachment_id.to_short_code(),
                         "filename": labels.get((card_id, attachment_id.to_short_code()), filename),
                         "snippet": body[:500]}
                    )
        return result

    def vector_document_sources(self, project, attachment_uids, *, context):
        """Read only current authorized attachment generations from the primary database."""
        import json
        from ....domain.models import CardMetadata
        from ....domain.services.factory.DoclingMetadataService import (
            DOCLING_DOCUMENTS_METADATA_KEY,
            DoclingMetadataService,
        )

        identifiers = []
        for uid in attachment_uids[:100]:
            try:
                identifiers.append(InfraHelper.convert_id(uid))
            except (ValueError, TypeError):
                continue
        if not identifiers:
            return {}
        statement = (
            select(CardAttachment, CardMetadata.value)
            .join(Card, Card.id == CardAttachment.card_id)
            .join(Project, Project.id == Card.project_id)
            .join(ProjectColumn, (ProjectColumn.id == Card.project_column_id)
                  & (ProjectColumn.project_id == Card.project_id))
            .join(CardMetadata, (CardMetadata.card_id == Card.id)
                  & (CardMetadata.key == DOCLING_DOCUMENTS_METADATA_KEY))
            .where(Card.project_id == InfraHelper.convert_id(project), card_visibility_scope(context))
            .where(Card.deleted_at.is_(None), Card.source_type.is_(None), Project.deleted_at.is_(None),
                   ProjectColumn.deleted_at.is_(None), CardAttachment.deleted_at.is_(None),
                   CardAttachment.id.in_(identifiers))
            .execution_options(populate_existing=True)
        )
        result = {}
        with DbSession.use(readonly=False) as db:
            for attachment, value in db.exec(statement).all():
                try:
                    documents = json.loads(value)
                    document = next(item for item in documents if isinstance(item, dict)
                                    and item.get("attachment_uid") == attachment.get_uid())
                    requested = document.get("requested_source")
                    if isinstance(requested, dict) and (
                        requested.get("file") != attachment.file.model_dump(mode="json")
                        or requested.get("content_sha256") != attachment.content_sha256
                    ):
                        continue
                    document = DoclingMetadataService.published_document(document, vectors=True)
                    embedding = document.get("embedding") or {}
                    pointer = embedding.get("pointer") or {}
                    published_hash = (document.get("vector_source") or {}).get("content_sha256", attachment.content_sha256)
                    if (document.get("status") != "indexed"
                        or not document.get("vector_source") and embedding.get("status") != "indexed"
                        or embedding.get("source_generation") != document.get("generation")
                        or pointer.get("board_uid") != SnowflakeID(InfraHelper.convert_id(project)).to_short_code()
                        or pointer.get("card_uid") != attachment.card_id.to_short_code()
                        or pointer.get("attachment_uid") != attachment.get_uid()
                        or pointer.get("source_type", "card_attachment") != "card_attachment"
                        or not published_hash
                        or pointer.get("content_hash") != published_hash
                        or document.get("content_hash") != published_hash):
                        continue
                    result[attachment.get_uid()] = {
                        "card_id": attachment.card_id, "filename": document.get("filename") or attachment.filename,
                        "document": document,
                    }
                except (ValueError, TypeError, StopIteration, AttributeError):
                    continue
        return result

    def search_document_matches(
        self, project: TProjectParam, card_ids: list[int], query: str, *, context: CardVisibilityContext
    ) -> dict[int, list[dict[str, str]]]:
        """Return bounded source excerpts, never entire attachment documents."""
        if not card_ids or not query:
            return {}
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        body = CardAttachment.document_text
        dialect = DbEngine.get_main_engine().dialect.name
        position = (
            func.strpos(func.lower(body), query.lower())
            if dialect == "postgresql"
            else func.instr(func.lower(body), query.lower())
        )
        if dialect in {"mysql", "mariadb"}:
            position = func.locate(query.lower(), func.lower(body))
        start = func.greatest(1, position - 100) if dialect != "sqlite" else func.max(1, position - 100)
        excerpt = func.substr(body, start, 500).label("excerpt")
        ranked = (
            select(
                CardAttachment.id,
                CardAttachment.card_id,
                CardAttachment.filename,
                excerpt,
                func.row_number()
                .over(partition_by=CardAttachment.card_id, order_by=CardAttachment.id)
                .label("source_rank"),
            )
            .join(Card, Card.id == CardAttachment.card_id)
            .join(
                ProjectColumn,
                (ProjectColumn.id == Card.project_column_id) & (ProjectColumn.project_id == Card.project_id),
            )
            .where(ProjectColumn.deleted_at.is_(None))
            .where(Card.project_id == InfraHelper.convert_id(project))
            .where(Card.id.in_(card_ids[:20]))
            .where(card_visibility_scope(context))
            .where(Card.deleted_at.is_(None))
            .where(CardAttachment.deleted_at.is_(None))
            .where(body != "")
            .where(body.ilike(pattern, escape="\\"))
            .subquery()
        )
        statement = (
            select(ranked.c.id, ranked.c.card_id, ranked.c.filename, ranked.c.excerpt)
            .where(ranked.c.source_rank <= 2)
            .order_by(ranked.c.card_id, ranked.c.id)
            .limit(40)
        )
        result: dict[int, list[dict[str, str]]] = {}
        with DbSession.use(readonly=False) as db:
            hits = db.exec(statement).all()
            labels = self._published_filenames(db, hits)
            for attachment_id, card_id, filename, snippet in hits:
                matches = result.setdefault(card_id, [])
                if len(matches) < 2:
                    matches.append(
                        {"attachment_uid": attachment_id.to_short_code(),
                         "filename": labels.get((card_id, attachment_id.to_short_code()), filename),
                         "snippet": snippet}
                    )
        return result

    def get_schedule_workload(self, user, start, end, as_of, *, contexts, personal):
        from .ScheduleWorkloadQuery import schedule_workload

        return schedule_workload(user, start, end, as_of, contexts=contexts, personal=personal)

    def get_schedule_page(self, user, start, end, limit, *, contexts, personal, after=None):
        """Authorize before range/keyset pagination; never load card or project bodies."""
        if not contexts:
            return []
        from ....domain.models.UserNotification import NotificationType, UserNotification

        user_id = InfraHelper.convert_id(user)
        with DbSession.use(readonly=False) as db:
            query = (
                SqlBuilder.select.tables(Card, Project, ProjectColumn)
                .join(Project, Project.id == Card.project_id)
                .join(
                    ProjectColumn,
                    (ProjectColumn.id == Card.project_column_id) & (ProjectColumn.project_id == Card.project_id),
                )
                .options(
                    load_only(
                        Card.id,
                        Card.project_id,
                        Card.project_column_id,
                        Card.title,
                        Card.priority,
                        Card.deadline_at,
                        Card.archived_at,
                        Card.source_type,
                        Card.source_uid,
                        Card.last_change_seq,
                        raiseload=True,
                    ),
                    load_only(Project.id, Project.title, raiseload=True),
                    load_only(ProjectColumn.id, ProjectColumn.name, raiseload=True),
                )
                .where(card_visibility_scope_by_project(contexts))
                .where(
                    Card.source_type.is_(None),
                    Card.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                    ProjectColumn.deleted_at.is_(None),
                )
                .where(Card.deadline_at >= start, Card.deadline_at < end)
            )
            if personal:
                assigned = (
                    select(CardAssignedUser.id)
                    .where(CardAssignedUser.card_id == Card.id, CardAssignedUser.user_id == user_id)
                    .exists()
                )
                # Keep historical mentions inside the database; transfer only the card page.
                # Match the native notification reference projection on supported dialects.
                dialect = DbEngine.get_main_engine().dialect.name
                if dialect == "sqlite":
                    refs = (
                        func.json_each(UserNotification.record_list).table_valued("key", "value").alias("schedule_refs")
                    )
                    ref_table = func.json_extract(refs.c.value, "$[0]")
                    ref_id = func.json_extract(refs.c.value, "$[1]")
                elif dialect == "postgresql":
                    refs = (
                        func.jsonb_array_elements(cast(UserNotification.record_list, JSONB))
                        .table_valued("value")
                        .alias("schedule_refs")
                    )
                    ref_table = refs.c.value.op("->>")(0)
                    ref_id = cast(refs.c.value.op("->>")(1), BigInteger)
                elif dialect in {"mysql", "mariadb"}:
                    refs = (
                        func.JSON_TABLE(
                            UserNotification.record_list,
                            literal_column(
                                "'$[*]' COLUMNS (ref_table VARCHAR(64) PATH '$[0]', ref_id BIGINT PATH '$[1]')"
                            ),
                        )
                        .table_valued("ref_table", "ref_id")
                        .alias("schedule_refs")
                    )
                    ref_table, ref_id = refs.c.ref_table, refs.c.ref_id
                else:
                    raise ValueError("Unsupported database dialect for Schedule mentions")
                mentioned = (
                    select(ref_id)
                    .select_from(UserNotification)
                    .join(refs, true())
                    .where(
                        UserNotification.receiver_id == user_id,
                        UserNotification.web_visible.is_(True),
                        UserNotification.notification_type.in_(
                            [NotificationType.MentionedInCard, NotificationType.MentionedInComment]
                        ),
                        ref_table == "card",
                    )
                )
                query = query.where(or_(assigned, Card.created_by_user_id == user_id, Card.id.in_(mentioned)))
            if after:
                deadline, project_id, card_id = after
                query = query.where(
                    or_(
                        Card.deadline_at > deadline,
                        (Card.deadline_at == deadline) & (Card.project_id > project_id),
                        (Card.deadline_at == deadline) & (Card.project_id == project_id) & (Card.id > card_id),
                    )
                )
            return db.exec(query.order_by(Card.deadline_at, Card.project_id, Card.id).limit(limit)).all()

    @staticmethod
    def _my_work_query(project_uids, contexts, *entities):
        """One authorized native work population for pages and aggregate counts."""
        return (
            select(*entities)
            .select_from(Card)
            .join(Project, Card.project_id == Project.id)
            .join(ProjectColumn, (Card.project_column_id == ProjectColumn.id) & (Card.project_id == ProjectColumn.project_id))
            .outerjoin(WorkflowStageDefinition, WorkflowStageDefinition.key == ProjectColumn.workflow_stage)
            .where(Project.id.in_([InfraHelper.convert_id(project) for project in project_uids]))
            .where(card_visibility_scope_by_project(contexts))
            .where(Card.deleted_at.is_(None), Project.deleted_at.is_(None), ProjectColumn.deleted_at.is_(None))
            .where(Card.archived_at.is_(None), ProjectColumn.is_archive.is_(False), Card.source_type.is_(None))
            .where(or_(WorkflowStageDefinition.id.is_(None), WorkflowStageDefinition.counts_as_completed.is_(False)))
            .where(or_(WorkflowStageDefinition.id.is_(None), WorkflowStageDefinition.active_queue_policy != "exclude"))
        )

    def get_my_work_context_page(
        self, user, project_uids, mentioned_card_ids, day_start, day_end, upcoming_end, group, limit, before,
        *, contexts, order="updated",
    ):
        """Count and page one statement snapshot without transferring card bodies."""
        if not contexts or not project_uids:
            return [], {}
        user_id = InfraHelper.convert_id(user)
        assigned = select(CardAssignedUser.id).where(
            CardAssignedUser.card_id == Card.id, CardAssignedUser.user_id == user_id
        ).exists()
        mentioned = Card.id.in_(set(mentioned_card_ids)) if mentioned_card_ids else false()
        overdue = (Card.deadline_at < day_start) & or_(
            WorkflowStageDefinition.id.is_(None), WorkflowStageDefinition.overdue_policy != "suppress"
        )
        work_group = case(
            (overdue, "overdue"),
            ((Card.deadline_at >= day_start) & (Card.deadline_at < day_end), "today"),
            ((Card.deadline_at >= day_end) & (Card.deadline_at < upcoming_end), "upcoming"),
            (ProjectColumn.workflow_stage == "review", "review"),
            (assigned, "assigned"),
            (mentioned, "mentioned"),
            else_="created",
        )
        population = self._my_work_query(
            project_uids, contexts, Card.id, Card.project_id, Card.updated_at, Card.deadline_at, assigned.label("is_assigned"),
            mentioned.label("is_mentioned"), work_group.label("work_group"),
        ).where(or_(assigned, mentioned, Card.created_by_user_id == user_id)).cte("my_work_population")
        groups = ("overdue", "today", "upcoming", "review", "assigned", "mentioned", "created")
        counts_query = select(*(
            func.count(case((population.c.work_group == key, 1))).label(key) for key in groups
        )).cte("my_work_counts")
        priority_groups = ("overdue", "today", "review", "upcoming", "assigned", "mentioned", "created")
        rank = case({key: index for index, key in enumerate(priority_groups)}, value=population.c.work_group)
        page = select(population, rank.label("priority_rank"))
        if group is not None:
            page = page.where(population.c.work_group == group)
        if before is not None:
            updated_at, project_id, card_id = before[-3:]
            updated_after = or_(
                population.c.updated_at < updated_at,
                (population.c.updated_at == updated_at) & (population.c.project_id < project_id),
                (population.c.updated_at == updated_at) & (population.c.project_id == project_id) & (population.c.id < card_id),
            )
            if order == "priority":
                before_rank, deadline = before[:2]
                deadline_equal = population.c.deadline_at.is_(None) if deadline is None else population.c.deadline_at == deadline
                deadline_after = false() if deadline is None else or_(
                    population.c.deadline_at > deadline, population.c.deadline_at.is_(None)
                )
                page = page.where(or_(
                    rank > before_rank,
                    (rank == before_rank) & deadline_after,
                    (rank == before_rank) & deadline_equal & updated_after,
                ))
            else:
                page = page.where(updated_after)

        def ordering(columns):
            prefix = (columns.priority_rank, columns.deadline_at.asc().nulls_last()) if order == "priority" else ()
            return (*prefix, columns.updated_at.desc(), columns.project_id.desc(), columns.id.desc())

        page = page.order_by(*ordering(page.selected_columns)).limit(limit).cte("my_work_page")
        # The aggregate always emits one row, including empty groups or exhausted cursors.
        query = select(
            Card, Project, ProjectColumn, page.c.is_assigned, page.c.is_mentioned, page.c.work_group,
            *counts_query.c,
        ).select_from(counts_query).outerjoin(page, true()).outerjoin(Card, Card.id == page.c.id).outerjoin(
            Project, Project.id == Card.project_id
        ).outerjoin(ProjectColumn, ProjectColumn.id == Card.project_column_id).options(
            load_only(Card.id, Card.title, Card.priority, Card.project_id, Card.project_column_id, Card.created_by_user_id,
                      Card.deadline_at, Card.updated_at, raiseload=True),
            load_only(Project.id, Project.title, raiseload=True),
            load_only(ProjectColumn.id, ProjectColumn.name, ProjectColumn.workflow_stage, raiseload=True),
        ).order_by(*ordering(page.c))
        with DbSession.use(readonly=False) as db:
            rows = db.exec(query).all()
        counts = {key: int(count) for key, count in zip(groups, rows[0][6:])}
        return [row[:6] for row in rows if row[0] is not None], counts

    def get_my_work_page(
        self,
        user: TUserParam,
        project_uids: Sequence[TProjectParam],
        purposes: set[str],
        mentioned_card_ids: Sequence[int],
        now: SafeDateTime,
        due_before: SafeDateTime,
        date_field: str,
        since: SafeDateTime | None,
        until: SafeDateTime | None,
        limit: int,
        before: tuple[SafeDateTime, int, int] | None = None,
        *,
        contexts: dict[int, CardVisibilityContext],
    ) -> list[tuple[Card, Project, ProjectColumn, bool]]:
        """Return one bounded cross-project page of user-focused, non-archived cards."""

        if not contexts:
            return []
        user_id = InfraHelper.convert_id(user)
        assigned = (
            select(CardAssignedUser.column("id"))
            .where(CardAssignedUser.column("card_id") == Card.column("id"))
            .where(CardAssignedUser.column("user_id") == user_id)
            .exists()
        )
        mentioned = Card.column("id").in_(set(mentioned_card_ids)) if mentioned_card_ids else false()
        mine = or_(assigned, Card.column("created_by_user_id") == user_id, mentioned)
        conditions: list[Any] = []
        if "assigned" in purposes:
            conditions.append(assigned)
        if "created" in purposes:
            conditions.append(Card.column("created_by_user_id") == user_id)
        if "mentioned" in purposes:
            conditions.append(mentioned)
        if "due_soon" in purposes:
            conditions.append(
                mine
                & Card.column("deadline_at").is_not(None)
                & (Card.column("deadline_at") >= now)
                & (Card.column("deadline_at") <= due_before)
            )
        if "overdue" in purposes:
            conditions.append(
                mine
                & Card.column("deadline_at").is_not(None)
                & (Card.column("deadline_at") < now)
                & or_(WorkflowStageDefinition.id.is_(None), WorkflowStageDefinition.overdue_policy != "suppress")
            )

        if date_field not in {"created_at", "updated_at"}:
            raise ValueError("date_field must be created_at or updated_at")
        if since is not None and until is not None and since >= until:
            raise ValueError("since must be earlier than until")
        date_column = Card.column(date_field)

        query = self._my_work_query(
            project_uids, contexts, Card, Project, ProjectColumn, assigned.label("is_assigned")
        ).where(or_(*conditions))
        if since is not None:
            query = query.where(date_column >= since)
        if until is not None:
            query = query.where(date_column < until)
        if before is not None:
            updated_at, project_id, card_id = before
            query = query.where(
                (Card.column("updated_at") < updated_at)
                | ((Card.column("updated_at") == updated_at) & (Project.column("id") < project_id))
                | (
                    (Card.column("updated_at") == updated_at)
                    & (Project.column("id") == project_id)
                    & (Card.column("id") < card_id)
                )
            )
        query = query.order_by(
            Card.column("updated_at").desc(), Project.column("id").desc(), Card.column("id").desc()
        ).limit(limit)
        with DbSession.use(readonly=False) as db:
            return db.exec(query).all()

    def get_page_by_project(
        self,
        project: TProjectParam,
        limit: int,
        before_updated_at: SafeDateTime | None = None,
        before_card: TCardParam | None = None,
        *,
        context: CardVisibilityContext,
        include_closed: bool = True,
        workflow_stages: list[str] | None = None,
        visibility: list[str] | None = None,
        created_by: list[str] | None = None,
        assigned_to: list[str] | None = None,
        priorities: list[str] | None = None,
        priority_order: bool = False,
        before_priority: str | None = None,
    ) -> list[tuple[Card, ProjectColumn]]:
        """Return a bounded newest-updated-first project-card keyset page."""

        project_id = InfraHelper.convert_id(project)
        query = (
            SqlBuilder.select.tables(Card, ProjectColumn)
            .join(ProjectColumn, Card.column("project_column_id") == ProjectColumn.column("id"))
            .where(Card.column("project_id") == project_id)
            .where(ProjectColumn.column("project_id") == project_id)
            .where(card_visibility_scope(context))
        )
        query = self._filter_project_workflow(query, include_closed, workflow_stages)
        query = self._filter_card_groups(query, context, visibility, created_by, assigned_to)
        query = self._filter_priorities(query, priorities)
        if type(priority_order) is not bool:
            raise ValueError("priority_order must be boolean")
        if before_updated_at is not None and priority_order:
            from ....domain.contracts.CardPriority import validate_card_priority
            validate_card_priority(before_priority)
        if before_updated_at is not None:
            if before_card is None:
                raise ValueError("before_card is required with before_updated_at")
            before_card_id = InfraHelper.convert_id(before_card)
            tie = (Card.updated_at < before_updated_at) | (
                (Card.updated_at == before_updated_at) & (Card.id < before_card_id)
            )
            query = query.where(
                (Card.priority > before_priority) | ((Card.priority == before_priority) & tie)
                if priority_order else tie
            )
        ordering = [Card.priority.asc()] if priority_order else []
        query = query.order_by(*ordering, Card.updated_at.desc(), Card.id.desc()).limit(limit + 1)
        with DbSession.use(readonly=False) as db:
            return list(db.exec(query).all())

    @staticmethod
    def _filter_project_workflow(query, include_closed: bool, workflow_stages: list[str] | None):
        if not include_closed:
            query = query.where(Card.archived_at.is_(None)).where(ProjectColumn.is_archive.is_(False))
            query = query.outerjoin(
                WorkflowStageDefinition, WorkflowStageDefinition.key == ProjectColumn.workflow_stage
            )
            query = query.where(
                or_(WorkflowStageDefinition.id.is_(None), WorkflowStageDefinition.counts_as_completed.is_(False))
            )
        if workflow_stages is not None:
            query = query.where(ProjectColumn.workflow_stage.in_(workflow_stages))
        return query

    @staticmethod
    def _filter_card_groups(query, context, visibility=None, created_by=None, assigned_to=None):
        if visibility is not None:
            query = query.where(Card.visibility.in_(visibility))

        def user_ids(values):
            return [context.actor_user_id if value == "me" else InfraHelper.convert_id(value) for value in values]

        if created_by is not None:
            query = query.where(Card.created_by_user_id.in_(user_ids(created_by)))
        if assigned_to is not None:
            query = query.where(
                select(CardAssignedUser.id)
                .where(
                    CardAssignedUser.card_id == Card.id,
                    CardAssignedUser.user_id.in_(user_ids(assigned_to)),
                )
                .exists()
            )
        return query

    @staticmethod
    def _filter_priorities(query, priorities):
        if priorities is not None:
            from ....domain.contracts.CardPriority import validate_card_priority
            if not isinstance(priorities, list) or len(priorities) > 4:
                raise ValueError("priorities must contain at most four priority values")
            query = query.where(Card.priority.in_([validate_card_priority(value) for value in priorities]))
        return query

    def count_by_project(
        self,
        project: TProjectParam,
        *,
        context: CardVisibilityContext,
        include_closed: bool = True,
        workflow_stages: list[str] | None = None,
        visibility: list[str] | None = None,
        created_by: list[str] | None = None,
        assigned_to: list[str] | None = None,
        priorities: list[str] | None = None,
    ) -> int:
        """Count with the same workflow filters as project pagination."""
        project_id = InfraHelper.convert_id(project)
        query = (
            SqlBuilder.select.count(Card, Card.column("id"))
            .join(ProjectColumn, Card.column("project_column_id") == ProjectColumn.column("id"))
            .where(Card.column("project_id") == project_id)
            .where(ProjectColumn.column("project_id") == project_id)
            .where(card_visibility_scope(context))
            .where(Card.column("deleted_at").is_(None))
            .where(ProjectColumn.column("deleted_at").is_(None))
        )
        query = self._filter_project_workflow(query, include_closed, workflow_stages)
        query = self._filter_card_groups(query, context, visibility, created_by, assigned_to)
        query = self._filter_priorities(query, priorities)
        with DbSession.use(readonly=False) as db:
            return db.exec(query).first() or 0

    def get_all_by_column(self, column: TColumnParam):
        column_id = InfraHelper.convert_id(column)

        records = []
        with DbSession.use(readonly=True) as db:
            result = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.column("project_column_id") == column_id)
                .order_by(Card.column("order").asc())
            )
            records = result.all()
        return records

    def move_all_by_column(
        self,
        source_column: TColumnParam,
        dest_column: TColumnParam,
        count_cards_in_source_column: int,
        is_archive: bool = False,
    ):
        source_column_id = InfraHelper.convert_id(source_column)
        dest_column_id = InfraHelper.convert_id(dest_column)
        current_time = SafeDateTime.now()

        with DbSession.use(readonly=False) as db:
            db.exec(
                SqlBuilder.update.table(Card)
                .values({Card.order: Card.order + count_cards_in_source_column})
                .where(Card.column("project_column_id") == dest_column_id)
                .where(Card.column("deleted_at").is_(None))
            )

            ordered_cards_cte = (
                SqlBuilder.select.columns(
                    Card.column("id"),
                    (func.row_number().over(order_by=Card.column("order")) - 1).label("new_order"),
                )
                .where(Card.column("project_column_id") == source_column_id)
                .where(Card.column("deleted_at").is_(None))
                .cte("ordered_cards")
            )

            db.exec(
                SqlBuilder.update.table(Card)
                .filter(Card.column("id") == ordered_cards_cte.c.id)
                .values(
                    {
                        Card.column("order"): ordered_cards_cte.c.new_order,
                        Card.column("project_column_id"): dest_column_id,
                        Card.column("archived_at"): current_time if is_archive else None,
                    }
                )
            )
