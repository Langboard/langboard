from typing import Any
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.types import SnowflakeID
from ....core.types.ParamTypes import TCardParam, TProjectParam, TUserOrBot
from ....helpers import InfraHelper
from ....publishers import CardRelationshipPublisher
from ....tasks.activities import CardRelationshipActivityTask
from ....tasks.bots import CardBotTask
from ...models import Card, CardRelationship, Project


class CardRelationshipService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "card_relationship"

    def get_api_list_by_card(self, card: TCardParam | None, limit: int | None = None) -> list[dict[str, Any]]:
        """Return relationships, optionally enforcing a repository row limit."""

        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return []

        raw_relationships = self.repo.card_relationship.get_all_by_card(card, limit=limit)
        relationships = [
            self._relationship_response(relationship, relationship_type)
            for relationship, relationship_type in raw_relationships
        ]
        return relationships

    def get_api_list_by_by_project(self, project: TProjectParam | None) -> list[dict[str, Any]]:
        project = InfraHelper.get_by_id_like(Project, project)
        if not project:
            return []

        raw_relationships = self.repo.card_relationship.get_all_by_project(project)
        relationships = [
            self._relationship_response(relationship, relationship_type)
            for relationship, relationship_type in raw_relationships
        ]
        return relationships

    @staticmethod
    def _relationship_response(relationship, relationship_type) -> dict[str, Any]:
        return {
            **relationship.api_response(),
            "machine_semantic": relationship_type.machine_semantic,
            "affects_readiness": relationship_type.affects_readiness,
            "is_system_default": relationship_type.is_system_default,
            "is_active": relationship_type.is_active,
        }

    def update(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        card: TCardParam | None,
        is_parent: bool,
        relationships: list[tuple[str, str]],
    ) -> list[dict[str, Any]] | None:
        with DbSession.atomic() as db:
            project_id = InfraHelper.convert_id(project)
            current_project = db.exec(
                SqlBuilder.select.table(Project).where(Project.id == project_id).with_for_update()
            ).first()
            if current_project is None or current_project.deleted_at:
                return None
            return self._update_relationships(db, user_or_bot, current_project, card, is_parent, relationships)

    def _update_relationships(self, db, user_or_bot, project, card, is_parent, relationships):
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params

        old_relationships = self.repo.card_relationship.get_all_by_card_and_relation(
            card, relation="parent" if is_parent else "child"
        )
        old_relationship_ids = [relationship.id for relationship, _, _ in old_relationships]

        converted_related_card_ids: set[SnowflakeID] = set()
        relationship_type_ids: set[SnowflakeID] = set()
        converted_relationships: list[tuple[SnowflakeID, SnowflakeID]] = []
        for related_card_uid, relationship_type_uid in relationships:
            related_card_id = SnowflakeID.from_short_code(related_card_uid)
            relationship_type_id = SnowflakeID.from_short_code(relationship_type_uid)
            converted_related_card_ids.add(related_card_id)
            relationship_type_ids.add(relationship_type_id)
            converted_relationships.append((related_card_id, relationship_type_id))

        related_card_ids = self.repo.card_relationship.get_all_related_card_ids(
            project, list(converted_related_card_ids)
        )

        relationship_types = self.repo.card_relationship.get_global_relationship_types_map(list(relationship_type_ids))

        candidates = []
        seen_related_ids = set()
        for related_card_id, relationship_type_id in converted_relationships:
            relation_type = relationship_types.get(relationship_type_id)
            if related_card_id not in related_card_ids or relation_type is None:
                raise ValueError("Related card or relationship type is unavailable")
            if related_card_id == card.id or not relation_type.is_active:
                raise ValueError("Self relationships and inactive relationship types are not allowed")
            if related_card_id in seen_related_ids:
                raise ValueError("Duplicate related card")
            seen_related_ids.add(related_card_id)
            candidates.append((related_card_id, relationship_type_id))
        edges = {
            (edge.card_id_parent, edge.card_id_child)
            for edge, relation_type in self.repo.card_relationship.get_all_by_project(project)
            if edge.id not in old_relationship_ids
            and relation_type.is_active
            and relation_type.machine_semantic == "blocks"
        }
        for related_card_id, type_id in candidates:
            if relationship_types[type_id].machine_semantic != "blocks":
                continue
            parent, child = (related_card_id, card.id) if is_parent else (card.id, related_card_id)
            pending, visited = [child], set()
            adjacency = {}
            for source, target in edges:
                adjacency.setdefault(source, []).append(target)
            while pending:
                node = pending.pop()
                if node == parent:
                    raise ValueError("Relationship would create a blocks cycle")
                if node not in visited:
                    visited.add(node)
                    pending.extend(adjacency.get(node, []))
            edges.add((parent, child))
        self.repo.card_relationship.delete_all_by_card_and_relation(card, relation="parent" if is_parent else "child")
        new_relationships_dict: dict[SnowflakeID, bool] = {}
        for related_card_id, relationship_type_id in candidates:
            new_relationship = CardRelationship(
                relationship_type_id=relationship_type_id,
                card_id_parent=related_card_id if is_parent else card.id,
                card_id_child=card.id if is_parent else related_card_id,
            )
            self.repo.card_relationship.insert(new_relationship)
            new_relationships_dict[related_card_id] = True

        new_relationships = self.get_api_list_by_card(card)

        def publish():
            CardRelationshipPublisher.updated(project, card, new_relationships)
            CardRelationshipActivityTask.card_relationship_updated(
                user_or_bot,
                project,
                card,
                old_relationship_ids,
                list(new_relationships_dict.keys()),
                is_parent,
            )
            CardBotTask.card_relationship_updated(user_or_bot, project, card)

        db.after_commit(publish)
        return new_relationships
