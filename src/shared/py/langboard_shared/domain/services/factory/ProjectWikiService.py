import hashlib
import re
from copy import deepcopy
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, cast, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import defer
from ....core.db import DbSession, EditorContentModel, SqlBuilder
from ....core.db.DbEngine import DbEngine
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.exceptions.WikiContentConflict import WikiContentConflict
from ....core.storage import FileModel
from ....core.types.ParamTypes import TProjectParam, TUserOrBot, TWikiParam
from ....core.utils.Converter import convert_python_data
from ....helpers import InfraHelper
from ....publishers import CardPublisher, ProjectWikiPublisher
from ....tasks.activities import ProjectWikiActivityTask
from ....tasks.bots import ProjectWikiBotTask
from ...models import (
    Bot,
    Card,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWiki,
    ProjectWikiActivity,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
)
from ...models.ProjectRole import ProjectRoleAction
from .GraphApprovalRequestService import GraphApprovalRequestService
from .NotificationService import NotificationService


class WikiSecretSelection(BaseModel):
    """Offsets address exact reviewed stored Markdown, measured in UTF16 code units."""

    model_config = ConfigDict(extra="forbid")
    start_utf16: int = Field(strict=True, ge=0)
    end_utf16: int = Field(strict=True, gt=0)


class WikiSecretSelectionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(strict=True, min_length=1, max_length=256)
    selection: WikiSecretSelection


class WikiSecretSelectionError(Exception):
    """Safe failure codes only; never wrap provider or SQL exception messages."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def wiki_secret_selection_bounds(content: str, selection: WikiSecretSelection) -> tuple[int, int]:
    offsets = {0: 0}
    units = 0
    for index, char in enumerate(content):
        units += 2 if ord(char) > 0xFFFF else 1
        offsets[units] = index + 1
    if (
        selection.start_utf16 not in offsets
        or selection.end_utf16 not in offsets
        or selection.start_utf16 >= selection.end_utf16
    ):
        raise WikiSecretSelectionError("invalid_selection")
    start, end = offsets[selection.start_utf16], offsets[selection.end_utf16]
    # A masked reference must never become credential material, even partially.
    for match in re.finditer(r"\[[^\]\n]*\]\(secret://[^)\n]*\)", content):
        if start < match.end() and end > match.start():
            raise WikiSecretSelectionError("unsupported_selection")
    # This increment supports one literal Markdown text span. Formatting delimiters,
    # links, code, escapes and block boundaries require a serializer-aware selector.
    line_start = content.rfind("\n", 0, start) + 1
    line_end = content.find("\n", end)
    line_end = len(content) if line_end < 0 else line_end
    line = content[line_start:line_end]
    if (
        "\n" in content[start:end]
        or re.search(r"[\r\[\]`*_~<>|\\]", line)
        or re.match(r"\s*(?:#{1,6} |>|[-+] |\d+[.)] |~~~|---)", line)
        or "secret://" in content[start:end]
    ):
        raise WikiSecretSelectionError("unsupported_selection")
    return start, end


class ProjectWikiService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "project_wiki"

    def prepare_secret_selection(
        self, db, actor: User, project_uid: str, wiki_uid: str, expected_revision: str, selection: WikiSecretSelection
    ):
        """Recheck native membership, read/write and wiki assignment under locks."""

        def locked(model, *conditions):
            return db.exec(
                SqlBuilder.select.table(model)
                .where(*conditions)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()

        if not isinstance(actor, User):
            raise WikiSecretSelectionError("unavailable")
        current = locked(User, User.id == actor.id)
        project = locked(Project, Project.id == InfraHelper.convert_id(project_uid))
        if current is None or not current.activated_at or project is None:
            raise WikiSecretSelectionError("unavailable")
        member = locked(
            ProjectAssignedUser, ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == current.id
        )
        role = locked(ProjectRole, ProjectRole.project_id == project.id, ProjectRole.user_id == current.id)
        if (
            project.owner_id != current.id
            and member is None
            or not current.is_admin
            and (role is None or not role.is_granted(ProjectRoleAction.Read))
        ):
            raise WikiSecretSelectionError("unavailable")
        wiki = locked(
            ProjectWiki, ProjectWiki.id == InfraHelper.convert_id(wiki_uid), ProjectWiki.project_id == project.id
        )
        if wiki is None:
            raise WikiSecretSelectionError("unavailable")
        assigned = locked(
            ProjectWikiAssignedUser,
            ProjectWikiAssignedUser.project_wiki_id == wiki.id,
            ProjectWikiAssignedUser.user_id == current.id,
        )
        if not wiki.is_public and not current.is_admin and assigned is None:
            raise WikiSecretSelectionError("unavailable")
        content = wiki.content.content
        if hashlib.sha256(content.encode()).hexdigest() != expected_revision:
            raise WikiSecretSelectionError("stale_revision")
        start, end = wiki_secret_selection_bounds(content, selection)
        return current, project, wiki, start, end

    def persist_secret_selection(
        self, db, actor: User, project: Project, wiki: ProjectWiki, before: str, after: str, selected: str
    ) -> None:
        """Mask proven historical content and commit the secrecy fence with the replacement."""
        wiki.content = EditorContentModel(content=after)
        if not self.repo.project_wiki.update_content_if_current(
            wiki, before, expected_generation=wiki.editor_generation, advance_generation=True
        ):
            raise WikiSecretSelectionError("stale_revision")
        # Keep historical bodies bounded while retaining the owner lock and atomic secrecy fence.
        after_id = 0
        while True:
            histories = db.exec(
                SqlBuilder.select.table(ProjectWikiActivity)
                .where(
                    ProjectWikiActivity.project_wiki_id == wiki.id,
                    ProjectWikiActivity.project_id == project.id,
                    ProjectWikiActivity.id > after_id,
                )
                .order_by(ProjectWikiActivity.id)
                .limit(100)
                .with_for_update()
            ).all()
            if not histories:
                break
            after_id = histories[-1].id
            for activity in histories:
                history = deepcopy(activity.activity_history)
                changed = False
                changes = history.get("changes")
                if not isinstance(changes, dict):
                    continue
                for side in ("before", "after"):
                    values = changes.get(side)
                    if not isinstance(values, dict):
                        continue
                    content = values.get("content")
                    if isinstance(content, dict) and isinstance(content.get("content"), str):
                        original = content["content"]
                        content["content"] = original.replace(selected, "[••••]")
                        changed |= original != content["content"]
                    elif isinstance(content, str):
                        values["content"] = content.replace(selected, "[••••]")
                        changed |= content != values["content"]
                if changed:
                    history["secret_redacted"] = True
                    # Retain the historical actor and timestamps while explicitly masking content.
                    db.exec(
                        update(ProjectWikiActivity)
                        .where(ProjectWikiActivity.id == activity.id)
                        .values(activity_history=history, updated_at=activity.updated_at)
                    )
        NotificationService.redact_wiki_mentions(db, wiki, selected)
        # This native event is the durable fence for older queued activity tasks.
        ProjectWikiActivityTask.record_wiki_secretized(actor, project, wiki)
        # No plaintext pre-state is retained in deferred task arguments or history.
        safe_wiki = wiki.model_copy(deep=True)
        model = {"content": {"content": after}, "editor_generation": wiki.editor_generation}

        def publish():
            ProjectWikiPublisher.updated(project, safe_wiki, model)
            self._publish_linked_card_changed(project, safe_wiki)
            ProjectWikiBotTask.project_wiki_updated(*ProjectWikiBotTask.wiki_event_args(actor, project, safe_wiki))

        db.after_commit(publish)

    def get_by_id_like(self, wiki: TWikiParam | None) -> ProjectWiki | None:
        return self.repo.project_wiki.get_by_id_like(wiki)

    def resolve_readable_file(
        self, actor: User, storage_type: str, storage_name: str, filename: str, *, metadata_only: bool = False
    ) -> ProjectWikiAttachment | None:
        """Authorize exact committed provenance; metadata projections forbid body loading."""
        dialect = DbEngine.get_main_engine().dialect.name
        column = ProjectWikiAttachment.column("file")
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
        with DbSession.use(readonly=False) as db:
            matches = db.exec(
                SqlBuilder.select.table(ProjectWikiAttachment, with_deleted=True)
                .where(
                    value("storage_type") == storage_type,
                    value("storage_name") == storage_name,
                    value("filename") == filename,
                )
                .limit(2)
                .execution_options(populate_existing=True)
            ).all()
            if len(matches) != 1:
                return None
            attachment = matches[0]
            if attachment.deleted_at is not None:
                if attachment.replaced_by_id is None or attachment.replaced_by_id == attachment.id:
                    return None
                original = attachment
                attachment = db.exec(SqlBuilder.select.table(ProjectWikiAttachment).where(
                    ProjectWikiAttachment.id == original.replaced_by_id,
                    ProjectWikiAttachment.project_wiki_id == original.project_wiki_id,
                    ProjectWikiAttachment.user_id == original.user_id,
                    ProjectWikiAttachment.replaced_by_id.is_(None),
                ).execution_options(populate_existing=True)).first()
                if attachment is None:
                    return None
                # The active locator must have unique provenance, including historical rows.
                from .ProjectWikiAttachmentService import wiki_file_identity
                owners = db.exec(SqlBuilder.select.table(ProjectWikiAttachment, with_deleted=True).where(
                    *wiki_file_identity(attachment.file)
                ).limit(2)).all()
                if len(owners) != 1 or owners[0].id != attachment.id:
                    return None
            elif attachment.replaced_by_id is not None:
                return None
            query = (
                select(ProjectWiki, Project, User)
                .select_from(ProjectWiki)
                .join(Project, Project.id == ProjectWiki.project_id)
                .join(User, User.id == actor.id)
                .where(ProjectWiki.id == attachment.project_wiki_id)
            )
            if metadata_only:
                query = query.options(defer(ProjectWiki.content, raiseload=True))
            current = db.exec(query.execution_options(populate_existing=True)).first()
            if current is None:
                return None
            wiki, project, user = current
            if (
                wiki.deleted_at is not None
                or project.deleted_at is not None
                or user.deleted_at is not None
                or not user.activated_at
            ):
                return None
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id,
                    ProjectAssignedUser.user_id == user.id,
                )
            ).first()
            if project.owner_id != user.id and member is None:
                return None
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project.id,
                    ProjectRole.user_id == user.id,
                ).execution_options(populate_existing=True)
            ).first()
            if not user.is_admin and (role is None or not role.is_granted(ProjectRoleAction.Read)):
                return None
            assigned = db.exec(
                SqlBuilder.select.column(ProjectWikiAssignedUser.user_id).where(
                    ProjectWikiAssignedUser.project_wiki_id == wiki.id,
                    ProjectWikiAssignedUser.user_id == user.id,
                    ProjectWikiAssignedUser.project_assigned_id == member.id if member else False,
                )
            ).all()
            return attachment if self.can_view(user, project, wiki, assigned) else None

    def get_api_list(self, user_or_bot: TUserOrBot, project: TProjectParam | None) -> list[dict[str, Any]]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return []

        raw_wikis = self.repo.project_wiki.get_all_by_project(project)
        wikis = [self.convert_to_api_response(user_or_bot, project, raw_wiki) for raw_wiki in raw_wikis]
        linked_cards = self.repo.card.get_linked_resource_map(
            project,
            Card.LINKED_RESOURCE_PROJECT_WIKI,
            [wiki.get_uid() for wiki in raw_wikis],
        )
        for raw_wiki, api_wiki in zip(raw_wikis, wikis, strict=True):
            linked_card = linked_cards.get(raw_wiki.get_uid())
            api_wiki["linked_card_uid"] = linked_card.get_uid() if linked_card else None

        return wikis

    def get_linked_card_uid(self, project: Project, wiki: ProjectWiki) -> str | None:
        card = self.repo.card.find_linked_resource(project, Card.LINKED_RESOURCE_PROJECT_WIKI, wiki.get_uid())
        return card.get_uid() if card else None

    def _publish_linked_card_changed(self, project: Project, wiki: ProjectWiki) -> None:
        card = self.repo.card.find_linked_resource(project, Card.LINKED_RESOURCE_PROJECT_WIKI, wiki.get_uid())
        if card:
            CardPublisher.linked_resource_changed(project, card)

    def convert_to_api_response(self, user_or_bot: TUserOrBot, project: Project, wiki: ProjectWiki) -> dict[str, Any]:
        api_wiki = wiki.api_response()
        api_wiki["assigned_members"] = []
        if wiki.is_public:
            return api_wiki

        assigned_users = self.repo.project_wiki_assigned_user.get_all_by_wiki(wiki)
        assigned_user_ids = [assigned_user.id for assigned_user, _ in assigned_users]

        is_showable = self.can_view(user_or_bot, project, wiki, assigned_user_ids)

        if is_showable:
            api_wiki["assigned_members"] = [assigned_user.api_response() for assigned_user, _ in assigned_users]
        else:
            api_wiki = wiki.convert_to_private_api_response()

        return api_wiki

    @staticmethod
    def can_view(user_or_bot: TUserOrBot, project: Project, wiki: ProjectWiki, assigned_user_ids: list[int]) -> bool:
        """Native wiki visibility shared by full content and bounded source projections."""
        return bool(
            wiki.is_public
            or (
                isinstance(user_or_bot, User)
                and (user_or_bot.is_admin or project.owner_id == user_or_bot.id or user_or_bot.id in assigned_user_ids)
            )
            or isinstance(user_or_bot, Bot)
        )

    def get_api_assigned_user_list(self, wiki: TWikiParam | None) -> list[dict[str, Any]]:
        wiki = InfraHelper.get_by_id_like(ProjectWiki, wiki)
        if not wiki:
            return []
        raw_users = self.repo.project_wiki_assigned_user.get_all_by_wiki(wiki)

        users = [user.api_response() for user, _ in raw_users]
        return users

    def is_assigned(self, user: User, wiki: TWikiParam | None) -> bool:
        if user.is_admin:
            return True

        wiki = InfraHelper.get_by_id_like(ProjectWiki, wiki)
        if not wiki:
            return False

        if wiki.is_public:
            return True

        record = self.repo.project_wiki_assigned_user.find_by_user_and_wiki(user, wiki)
        return bool(record)

    def create(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        title: str,
        content: EditorContentModel | None = None,
    ) -> tuple[ProjectWiki, dict[str, Any]] | None:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return None

        max_order = InfraHelper.get_next_order(ProjectWiki, "project_id", project.id)

        wiki = ProjectWiki(
            project_id=project.id,
            title=title,
            content=content or EditorContentModel(),
            order=self.repo.project_wiki.get_next_order(max_order),
        )
        self.repo.project_wiki.insert(wiki)

        api_wiki = wiki.api_response()
        api_wiki["assigned_members"] = []
        ProjectWikiPublisher.created(project, wiki)
        ProjectWikiActivityTask.project_wiki_created(user_or_bot, project, wiki)
        ProjectWikiBotTask.project_wiki_created(*ProjectWikiBotTask.wiki_event_args(user_or_bot, project, wiki))

        return wiki, api_wiki

    def update(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        wiki: TWikiParam | None,
        form: dict,
        *,
        expected_content: str | None = None,
        expected_generation: int | None = None,
        expected_revision: str | None = None,
        advance_generation: bool = False,
    ) -> dict[str, Any] | Literal[True] | None:
        if expected_content is not None and set(form) != {"content"}:
            raise ValueError("Conditional wiki edits may change only content")
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params

        validators: TMutableValidatorMap = {"title": "not_empty", "content": "default"}

        if "content" in form:
            if type(expected_generation) is not int or expected_generation < 0:
                raise WikiContentConflict("Refresh the editor before saving content")
            if expected_content is None:
                if (
                    expected_revision is None
                    or hashlib.sha256(wiki.content.content.encode()).hexdigest() != expected_revision
                ):
                    raise WikiContentConflict("Wiki changed after review")
                expected_content = wiki.content.content
            if wiki.editor_generation != expected_generation:
                raise WikiContentConflict("Editor lineage changed; refresh the editor")

        old_record = self.apply_mutates(wiki, form, validators)
        if not old_record:
            if expected_content is not None and not self.repo.project_wiki.update_content_if_current(
                wiki, expected_content, expected_generation=expected_generation
            ):
                raise WikiContentConflict("Wiki changed after review")
            return True

        if expected_content is None:
            self.repo.project_wiki.update(wiki)
        elif not self.repo.project_wiki.update_content_if_current(
            wiki,
            expected_content,
            expected_generation=expected_generation,
            advance_generation=advance_generation,
            title=wiki.title if "title" in form else None,
        ):
            raise WikiContentConflict("Wiki changed after review; no append saved")

        model: dict[str, Any] = {}
        for key in form:
            if key not in validators or key not in old_record:
                continue
            model[key] = convert_python_data(getattr(wiki, key))

        if "content" in model:
            model["editor_generation"] = wiki.editor_generation
        ProjectWikiPublisher.updated(project, wiki, model)
        self._publish_linked_card_changed(project, wiki)

        notification_service = self._get_service(NotificationService)
        if "content" in model:
            notification_service.notify_mentioned_in_wiki(user_or_bot, project, wiki)

        ProjectWikiActivityTask.project_wiki_updated(user_or_bot, project, old_record, wiki)
        ProjectWikiBotTask.project_wiki_updated(*ProjectWikiBotTask.wiki_event_args(user_or_bot, project, wiki))

        return model

    def change_public(
        self, user_or_bot: TUserOrBot, project: TProjectParam | None, wiki: TWikiParam | None, is_public: bool
    ) -> tuple[ProjectWiki, Project] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params

        if is_public:
            self.repo.project_wiki_assigned_user.delete_all_by_wiki(wiki)
        else:
            if isinstance(user_or_bot, Bot):
                return None

            project_assigned = self.repo.project_assigned_user.find_by_user_and_project(user_or_bot, project)
            if not project_assigned:
                return None

            model_params: dict[str, Any] = {
                "project_assigned_id": project_assigned.id,
                "project_wiki_id": wiki.id,
            }
            model_params["user_id"] = user_or_bot.id
            assigned_user = ProjectWikiAssignedUser(**model_params)
            self.repo.project_wiki_assigned_user.insert(assigned_user)

        was_public = wiki.is_public
        wiki.is_public = is_public

        self.repo.project_wiki.update(wiki)

        ProjectWikiPublisher.publicity_changed(user_or_bot, project, wiki)
        self._publish_linked_card_changed(project, wiki)
        ProjectWikiActivityTask.project_wiki_publicity_changed(user_or_bot, project, was_public, wiki)
        ProjectWikiBotTask.project_wiki_publicity_changed(
            *ProjectWikiBotTask.wiki_event_args(user_or_bot, project, wiki)
        )

        return wiki, project

    def update_assignees(
        self, user: User, project: TProjectParam | None, wiki: TWikiParam | None, assign_user_uids: list[str]
    ) -> tuple[ProjectWiki, Project] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params
        if wiki.is_public:
            return None

        old_assigned_users = self.repo.project_wiki_assigned_user.get_all_by_wiki(wiki)

        self.repo.project_wiki_assigned_user.delete_all_by_wiki(wiki)

        target_users: list[User] = []
        if assign_user_uids:
            project_members = self.repo.project_assigned_user.get_all_by_project(
                project, where_users_in=assign_user_uids
            )

            for target_user, project_assigned_user in project_members:
                assigned_user = ProjectWikiAssignedUser(
                    project_assigned_id=project_assigned_user.id, project_wiki_id=wiki.id, user_id=target_user.id
                )
                self.repo.project_wiki_assigned_user.insert(assigned_user)
                target_users.append(target_user)

        ProjectWikiPublisher.assignees_updated(project, wiki, target_users)
        self._publish_linked_card_changed(project, wiki)
        ProjectWikiActivityTask.project_wiki_assignees_updated(
            user,
            project,
            wiki,
            [target_user.id for target_user, _ in old_assigned_users],
            [target_user.id for target_user in target_users],
        )

        return wiki, project

    def change_order(
        self, project: TProjectParam | None, wiki: TWikiParam | None, order: int
    ) -> tuple[Project, ProjectWiki] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params

        old_order = wiki.order
        wiki.order = order
        self.repo.project_wiki.update_column_order(wiki, project, old_order, order)

        ProjectWikiPublisher.order_changed(project, wiki)

        return project, wiki

    def can_upload_attachment(self, user, project, wiki):
        from .ProjectAttachmentService import current_authority

        with DbSession.use(readonly=False) as db:
            authority = current_authority(db, user, project)
            if authority is None:
                return False
            actor, board, role = authority
            current = db.exec(
                SqlBuilder.select.table(ProjectWiki)
                .where(ProjectWiki.id == InfraHelper.convert_id(wiki), ProjectWiki.project_id == board.id)
                .execution_options(populate_existing=True)
            ).first()
            if current is None or (
                not actor.is_admin and (role is None or not role.is_granted(ProjectRoleAction.Read))
            ):
                return False
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == board.id, ProjectAssignedUser.user_id == actor.id
                )
            ).first()
            assigned = db.exec(
                SqlBuilder.select.column(ProjectWikiAssignedUser.user_id).where(
                    ProjectWikiAssignedUser.project_wiki_id == current.id,
                    ProjectWikiAssignedUser.user_id == actor.id,
                    ProjectWikiAssignedUser.project_assigned_id == member.id if member else False,
                )
            ).all()
            return self.can_view(actor, board, current, assigned)

    def upload_attachment(
        self, user: User, project: TProjectParam | None, wiki: TWikiParam | None, attachment: FileModel
    ) -> ProjectWikiAttachment | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return None
        project, wiki = params

        wiki_attachment = ProjectWikiAttachment(
            user_id=user.id,
            project_wiki_id=wiki.id,
            filename=attachment.original_filename,
            file=attachment,
            order=self.repo.project_wiki_attachment.get_next_order(wiki),
        )

        from ..AppGovernance import lock_policy_mutation
        from .WikiDocumentService import WikiDocumentService

        if not self.can_upload_attachment(user, project, wiki):
            return None
        documents = self._get_service(WikiDocumentService)
        digest = documents.hash_file(attachment)
        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            db.exec(SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == wiki.id).with_for_update()).first()
            if not self.can_upload_attachment(user, project, wiki):
                return None
            wiki_attachment.content_sha256 = digest
            self.repo.project_wiki_attachment.insert(wiki_attachment)

            # Processing is optional; the native upload must survive invalid model settings.
            def process_after_commit():
                try:
                    documents.request(user, project, wiki_attachment, automatic=True)
                except Exception:
                    from ....core.broker import Broker

                    Broker.logger.warning("Wiki document automatic processing unavailable")

            db.after_commit(process_after_commit)
        return wiki_attachment

    def delete(self, user_or_bot: TUserOrBot, project: TProjectParam | None, wiki: TWikiParam | None) -> bool:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (ProjectWiki, wiki))
        if not params:
            return False
        project, wiki = params

        self._get_service(GraphApprovalRequestService).cancel_pending_by_scope(
            project, ProjectWiki.__tablename__, wiki.get_uid(), reason="project wiki deleted"
        )
        from .WikiDocumentService import WikiDocumentService

        with DbSession.atomic():
            self._get_service(WikiDocumentService).record_scope_embedding_cleanup(project, wiki)
            self.repo.project_wiki.delete(wiki)

        ProjectWikiPublisher.deleted(project, wiki)
        self._publish_linked_card_changed(project, wiki)
        ProjectWikiActivityTask.project_wiki_deleted(user_or_bot, project, wiki)
        ProjectWikiBotTask.project_wiki_deleted(*ProjectWikiBotTask.wiki_event_args(user_or_bot, project, wiki))

        return True
