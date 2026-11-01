from datetime import timedelta
from typing import Any, Literal, TypeVar, overload
from ....core.db import BaseDbModel
from ....core.domain import BaseDomainService
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime
from ....core.types.ParamTypes import TProjectParam, TUserOrBot
from ....helpers import InfraHelper
from ...constants.CardPresentation import CARD_PRESENTATION_KEY, validate_card_presentation
from ...models import Card, CardMetadata
from ...models.bases import BaseMetadataModel
from ..CardAppMutation import guard_card_app_mutation


_TMetadata = TypeVar("_TMetadata", bound=BaseMetadataModel)


class MetadataService(BaseDomainService):
    @guard_card_app_mutation
    def save_card(
        self,
        user_or_bot: TUserOrBot,
        project,
        card: Card,
        key: str,
        value: str,
        old_key: str | None = None,
        *,
        internal: bool = False,
    ):
        """Authenticated card write boundary; internal keys do not bypass ownership."""
        if card.project_id != project.id:
            raise ValueError("Card not found in project")
        return self.save(CardMetadata, card, key, value, old_key, internal=internal)

    @guard_card_app_mutation
    def delete_card(self, user_or_bot: TUserOrBot, project, card: Card, keys: str | list[str]) -> bool:
        if card.project_id != project.id:
            raise ValueError("Card not found in project")
        return self.delete(CardMetadata, card, keys)

    @staticmethod
    def _is_work_plan_receipt(key: str) -> bool:
        return (
            key.strip()
            .lower()
            .startswith(
                (
                    "internal.work_plan.",
                    "internal.comment_compaction.",
                    "internal.comment_request.",
                )
            )
        )

    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "metadata"

    @overload
    def get_all_as_api(
        self,
        model: type[_TMetadata],
        foreign_model: BaseDbModel,
        as_dict: Literal[False],
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...
    @overload
    def get_all_as_api(
        self,
        model: type[_TMetadata],
        foreign_model: BaseDbModel,
        as_dict: Literal[True],
        limit: int | None = None,
    ) -> dict[str, Any]: ...
    def get_all_as_api(
        self,
        model: type[_TMetadata],
        foreign_model: BaseDbModel,
        as_dict: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return metadata, optionally enforcing a repository row limit."""

        if isinstance(foreign_model, Card) and foreign_model.is_linked_resource:
            return {} if as_dict else []

        metadata_list = self.repo.metadata.get_list(model, foreign_model, limit=limit)
        metadata_list = [m for m in metadata_list if not self._is_work_plan_receipt(m.key)]
        responses = [metadata.api_response() for metadata in metadata_list]
        self._project_documents(responses)
        if not as_dict:
            return responses

        metadata = {}
        for data in responses:
            metadata[data["key"]] = data["value"]
        return metadata

    def get_project_cards_as_api(
        self,
        project: TProjectParam,
        user: TUserOrBot,
        channel: CollaborationChannel = CollaborationChannel.Api,
        *,
        board_visible_only: bool = False,
    ) -> dict[str, dict[str, str]]:
        resolved = self._get_service_by_name("card").resolve_visibility_context(project, user, channel)
        if resolved is None:
            return {}
        project, context = resolved
        cutoff = SafeDateTime.now() - timedelta(days=project.archive_visible_days) if board_visible_only else None
        records: dict[str, dict[str, str]] = {}
        for card_id, key, value in self.repo.metadata.get_by_project_cards(project, context=context, archive_visible_since=cutoff):
            if self._is_work_plan_receipt(key):
                continue
            records.setdefault(InfraHelper.convert_uid(card_id), {})[key] = value
        rows = [
            {"key": key, "value": value, "card_uid": uid}
            for uid, metadata in records.items()
            for key, value in metadata.items()
        ]
        self._project_documents(rows)
        for row in rows:
            records[row["card_uid"]][row["key"]] = row["value"]
        return records

    def get_by_key_as_api(
        self, model: type[_TMetadata], foreign_model: BaseDbModel, key: str, *, internal: bool = False
    ) -> dict[str, Any] | None:
        if self._is_work_plan_receipt(key) and not internal:
            return None
        if isinstance(foreign_model, Card) and foreign_model.is_linked_resource:
            return None
        metadata = self.repo.metadata.get_by_key(model, foreign_model, key)
        if metadata is None:
            return None
        response = metadata.api_response()
        if not internal:
            self._project_documents([response])
        return response

    @staticmethod
    def _project_documents(rows: list[dict[str, Any]]) -> None:
        from .DoclingMetadataService import DOCLING_DOCUMENTS_METADATA_KEY, DoclingMetadataService

        documents = [row for row in rows if row["key"] == DOCLING_DOCUMENTS_METADATA_KEY]
        if not documents:
            return
        current = DoclingMetadataService.current_embedding_config()
        for row in documents:
            row["value"] = DoclingMetadataService.project_api_documents(row["value"], current)

    def save(
        self,
        model: type[_TMetadata],
        foreign_model: BaseDbModel,
        key: str,
        value: str,
        old_key: str | None = None,
        *,
        internal: bool = False,
    ) -> _TMetadata | None:
        if isinstance(foreign_model, Card) and foreign_model.is_linked_resource:
            return None
        if not internal and any(self._is_work_plan_receipt(k) for k in (key, old_key) if k is not None):
            raise ValueError("Derived internal metadata is server-owned")
        if isinstance(foreign_model, Card) and key == CARD_PRESENTATION_KEY:
            validate_card_presentation(value)
        metadata = self.repo.metadata.save(model, foreign_model, key, value, old_key)
        return metadata

    def delete(self, model: type[_TMetadata], foreign_model: BaseDbModel, keys: str | list[str]) -> bool:
        if isinstance(foreign_model, Card) and foreign_model.is_linked_resource:
            return False
        if any(self._is_work_plan_receipt(k) for k in ([keys] if isinstance(keys, str) else keys)):
            raise ValueError("Derived internal metadata is server-owned")
        return self.repo.metadata.delete_keys(model, foreign_model, keys)
