from typing import Any
from sqlalchemy import select
from ....core.db import DbSession, EditorContentModel, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.exceptions.RelationshipCycle import RelationshipCycle
from ....core.types import SnowflakeID
from ....core.types.ParamTypes import TCardParam, TProjectParam, TUserOrBot
from ....helpers import InfraHelper
from ....publishers import CardPublisher, CardRelationshipPublisher
from ....tasks.activities import CardActivityTask, CardRelationshipActivityTask
from ....tasks.bots import CardBotTask
from ....tasks.webhooks.ExecutionReadinessUow import execution_readiness_uow
from ...models import Bot, Card, CardRelationship, Project, ProjectColumn, User
from ..CardAppMutation import require_card_app_mutation
from ..CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel


class CardRelationshipService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "card_relationship"

    def get_api_relation_summaries(
        self,
        project: TProjectParam,
        card: TCardParam,
        user_or_bot: TUserOrBot,
        *,
        channel: CollaborationChannel = CollaborationChannel.Api,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one authorized node and a bounded page of its immediate edges."""
        from .CardService import CardService

        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid relationship summary limit")
        service = self._get_service(CardService)
        resolved = service.resolve_readable_card(project, card, user_or_bot, channel, metadata_only=True)
        if resolved is None:
            return None
        project, card, _ = resolved
        contexts = service.resolve_work_visibility_contexts(user_or_bot, channel)
        if project.id not in contexts:
            return None
        after_id = 0
        if cursor is not None:
            parts = cursor.split(".")
            if len(cursor) != 25 or len(parts) != 3 or parts[:2] != ["1", card.get_uid()]:
                raise ValueError("Invalid relationship summary cursor")
            after_id = SnowflakeID.from_short_code(parts[2])
            if after_id <= 0 or after_id.to_short_code() != parts[2]:
                raise ValueError("Invalid relationship summary cursor")
        rows = self.repo.card_relationship.get_summary_page(card, contexts, limit=limit + 1, after_id=after_id)
        items = []
        for edge, kind, target_id, title, project_id, project_title in rows[:limit]:
            items.append(
                {
                    "relationship": self.public_relationship(edge, kind),
                    "target": {
                        "uid": SnowflakeID(target_id).to_short_code(),
                        "title": title,
                        "project_uid": SnowflakeID(project_id).to_short_code(),
                        "project_title": project_title,
                    },
                }
            )
        return {
            "card": {"uid": card.get_uid(), "title": card.title, "project_uid": project.get_uid()},
            "items": items,
            "next_cursor": f"1.{card.get_uid()}.{rows[limit - 1][0].get_uid()}" if len(rows) > limit else None,
            "limit": limit,
        }

    def get_api_list_by_card(self, card: TCardParam | None, limit: int | None = None, *, context: CardVisibilityContext | None = None) -> list[dict[str, Any]]:
        """Return relationships, optionally enforcing a repository row limit."""

        card = InfraHelper.get_by_id_like(Card, card)
        if not card:
            return []

        if context is not None:
            if limit is not None:
                if limit <= 0:
                    return []
                # Count authorized rows, never raw edges. Keyset batches keep
                # hidden prefixes from truncating MCP's visible overflow probe.
                result, after_id = [], 0
                batch_size = min(limit, 100)
                while len(result) < limit:
                    edges = self.repo.card_relationship.get_incident_by_cards([card], limit=batch_size, after_id=after_id)
                    if not edges:
                        break
                    projected = self.get_readable_incident_by_cards([card], context, edges=edges)
                    result.extend(projected.get(card.id, []))
                    after_id = edges[-1][0].id
                    if len(edges) < batch_size:
                        break
                return result[:limit]
            projected = self.get_readable_incident_by_cards([card], context, limit=limit)
            rows = projected.get(card.id, [])
            return rows[:limit] if limit is not None else rows

        scope = {"context": context} if context is not None else {}
        raw_relationships = self.repo.card_relationship.get_all_by_card(card, limit=limit, **scope)
        relationships = [
            self._relationship_response(relationship, relationship_type)
            for relationship, relationship_type in raw_relationships
        ]
        return relationships

    def get_readable_incident_by_cards(self, cards, context, *, limit=None, board_project_id=None, edges=None):
        """Project readable endpoints with their current board location in one bounded batch."""
        from ..CardVisibilityPolicy import CardVisibility
        from .CardService import CardService

        if context is None or context.actor_user_id is None:
            return {}
        if edges is None:
            edges = self.repo.card_relationship.get_incident_by_cards(cards, limit=limit)
        endpoint_ids = {identifier for edge, _ in edges for identifier in (edge.card_id_parent, edge.card_id_child)}
        if not endpoint_ids:
            return {}
        with DbSession.use(readonly=False) as db:
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == context.actor_user_id)).first()
            endpoints = db.exec(SqlBuilder.select.table(Card).where(
                Card.id.in_(endpoint_ids), Card.deleted_at.is_(None)).execution_options(populate_existing=True)).all()
        service = self._get_service(CardService)
        contexts = service.resolve_work_visibility_contexts(actor, context.channel,
                                                            project_ids={row.project_id for row in endpoints})
        with DbSession.use(readonly=False) as db:
            projects = {row.id: row for row in db.exec(SqlBuilder.select.table(Project).where(
                Project.id.in_(contexts), Project.deleted_at.is_(None))).all()}
        visible = {row.id: row for row in endpoints if row.project_id in projects
                   and contexts[row.project_id].can_read_card(row.visibility, owner_user_id=row.owner_user_id)}
        local_ids = {InfraHelper.convert_id(card) for card in cards}
        result = {}
        for edge, kind in edges:
            parent, child = visible.get(edge.card_id_parent), visible.get(edge.card_id_child)
            if parent is None or child is None:
                continue
            if board_project_id is not None and any(
                row.project_id == board_project_id and row.id not in local_ids for row in (parent, child)
            ):
                continue
            # PRIVATE relationships retain the same-owner rule across locations.
            if CardVisibility.Private in (parent.visibility, child.visibility) and not (
                parent.visibility == child.visibility == CardVisibility.Private and parent.owner_user_id == child.owner_user_id
            ):
                continue
            payload = self.public_relationship(edge, kind)
            if parent.project_id != child.project_id:
                payload["parent_location"] = {"project_uid": projects[parent.project_id].get_uid(),
                                               "project_title": projects[parent.project_id].title}
                payload["child_location"] = {"project_uid": projects[child.project_id].get_uid(),
                                              "project_title": projects[child.project_id].title}
            for endpoint in (parent.id, child.id):
                if endpoint in local_ids:
                    result.setdefault(endpoint, []).append(payload)
        return result

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
            "parent_name": relationship_type.parent_name,
            "child_name": relationship_type.child_name,
            "machine_semantic": relationship_type.machine_semantic,
            "affects_readiness": relationship_type.affects_readiness,
            "is_system_default": relationship_type.is_system_default,
            "is_active": relationship_type.is_active,
        }

    # Preserve the public SDK projection while sharing the upstream response contract.
    public_relationship = _relationship_response

    def _mark_card_changed_for_unread(self, card, target_type: str, target_id=None) -> None:
        """Stamp the unread cursor for this card change (lazy import avoids cycles)."""
        from .CardService import CardService

        card_service = self._get_service(CardService)
        card_service.mark_card_changed(card, target_type, target_id)

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
                SqlBuilder.select.table(Project).where(Project.id == project_id).with_for_update(key_share=True)
            ).first()
            if current_project is None or current_project.deleted_at:
                return None
            return self._update_relationships(db, user_or_bot, current_project, card, is_parent, relationships)

    def _update_relationships(self, db, user_or_bot, project, card, is_parent, relationships):
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, card))
        if not params:
            return None
        project, card = params
        if card.project_id != project.id or card.is_linked_resource:
            return None
        require_card_app_mutation(db, user_or_bot, card, project)

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
        affected_ids = {related.id for _, _, related in old_relationships} | set(related_card_ids)
        for affected_id in sorted(affected_ids):
            require_card_app_mutation(db, user_or_bot, affected_id, project)
        edges = {
            (edge.card_id_parent, edge.card_id_child)
            for edge, relation_type in self.repo.card_relationship.get_all_by_project(project)
            if edge.id not in old_relationship_ids
            and relation_type.is_active
            and relation_type.machine_semantic == "blocks"
        }
        for related_card_id, type_id in candidates:
            parent, child = (related_card_id, card.id) if is_parent else (card.id, related_card_id)
            self._validate_blocking_edge(edges, parent, child, relationship_types[type_id].machine_semantic)
        with execution_readiness_uow() as execution:
            execution.watch([card.id] if is_parent else affected_ids)
            retained = {(related.id, edge.relationship_type_id): edge for edge, _, related in old_relationships}
            desired = set(candidates)
            removed_ids = [edge.id for key, edge in retained.items() if key not in desired]
            if removed_ids:
                db.exec(SqlBuilder.delete.table(CardRelationship).where(CardRelationship.id.in_(removed_ids)))
            new_relationships_dict: dict[SnowflakeID, bool] = {}
            for related_card_id, relationship_type_id in candidates:
                new_relationships_dict[related_card_id] = True
                if (related_card_id, relationship_type_id) in retained:
                    continue
                new_relationship = CardRelationship(
                    relationship_type_id=relationship_type_id,
                    card_id_parent=related_card_id if is_parent else card.id,
                    card_id_child=card.id if is_parent else related_card_id,
                )
                self.repo.card_relationship.insert(new_relationship)

            new_relationships = self.get_api_list_by_card(card)
            self._mark_card_changed_for_unread(card, "relationship")

        def publish():
            from .CardService import CardService

            self._get_service(CardService).publish_work_states(project, [card.id, *affected_ids])
            self.dispatch_updated(
                user_or_bot, project, card, old_relationship_ids,
                list(new_relationships_dict.keys()), is_parent, relationships=new_relationships,
            )

        db.after_commit(publish)
        return new_relationships

    def dispatch_updated(
        self,
        user_or_bot: TUserOrBot,
        project: Project,
        card: Card,
        old_relationship_ids: list[SnowflakeID],
        new_related_card_ids: list[SnowflakeID],
        is_parent: bool,
        *,
        relationships: list[dict[str, Any]] | None = None,
        include_bot: bool = True,
    ) -> None:
        CardRelationshipPublisher.updated(project, card)
        CardRelationshipActivityTask.card_relationship_updated(
            user_or_bot, project, card, old_relationship_ids, new_related_card_ids, is_parent
        )
        if include_bot:
            CardBotTask.card_relationship_updated(user_or_bot, project, card)

    def apply_graph_patch(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        anchor_card: TCardParam | None,
        new_cards: list[tuple[str, str, str | None]],
        add_edges: list[tuple[str, str, str]],
        remove_relationship_uids: list[str],
        *,
        dispatch_effects: bool = True,
    ) -> dict[str, Any] | None:
        """Atomically create cards and patch typed relationships around an anchor card."""

        with DbSession.atomic():
            return self._apply_graph_patch(
                user_or_bot, project, anchor_card, new_cards, add_edges, remove_relationship_uids,
                dispatch_effects=dispatch_effects,
            )

    def preview_graph_patch(self, user_or_bot, project, anchor_card, new_cards, add_edges, remove_relationship_uids):
        """Validate the current graph without allocating or persisting records."""
        with DbSession.atomic():
            return self._apply_graph_patch(
                user_or_bot, project, anchor_card, new_cards, add_edges, remove_relationship_uids,
                dispatch_effects=False, dry_run=True,
            )

    def _apply_graph_patch(
        self,
        user_or_bot: TUserOrBot,
        project: TProjectParam | None,
        anchor_card: TCardParam | None,
        new_cards: list[tuple[str, str, str | None]],
        add_edges: list[tuple[str, str, str]],
        remove_relationship_uids: list[str],
        *,
        dispatch_effects: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any] | None:
        params = InfraHelper.get_records_with_foreign_by_params((Project, project), (Card, anchor_card))
        if not params:
            return None
        project, anchor_card = params
        if anchor_card.project_id != project.id:
            return None
        # Serialize validation and persistence against the project's current graph.
        # NO KEY UPDATE retains that serialization while allowing child-row FK checks.
        # Nested repository and readiness calls share this transaction.
        with DbSession.atomic() as db:
            project_row = db.exec(
                select(Project.column("id")).where(Project.column("id") == project.id).with_for_update(key_share=True)
            ).first()
            if project_row is None:
                return None
            require_card_app_mutation(db, user_or_bot, anchor_card, project)
            column = InfraHelper.get_by_id_like(ProjectColumn, anchor_card.project_column_id)
            if not column or column.project_id != project.id or column.is_archive:
                raise ValueError("Anchor card must be in an active project column")

            new_refs = {client_ref for client_ref, _, _ in new_cards}
            referenced = {ref for edge in add_edges for ref in edge[:2]}
            unknown_new_refs = sorted(ref for ref in referenced if ref.startswith("new:") and ref not in new_refs)
            if unknown_new_refs:
                raise ValueError(f"Unknown new card reference: {unknown_new_refs[0]}")

            existing_refs = {ref for ref in referenced if not ref.startswith("new:")}
            existing_refs.add(anchor_card.get_uid())
            existing_cards: dict[str, Card] = {}
            for card_uid in existing_refs:
                card = InfraHelper.get_by_id_like(Card, card_uid)
                if not card or card.project_id != project.id:
                    raise ValueError(f"Unknown project card: {card_uid}")
                existing_cards[card_uid] = card

            graph_snapshot = self.repo.card_relationship.get_graph_snapshot(project)
            relationship_by_uid = {
                SnowflakeID(relationship_id).to_short_code(): (relationship_id, parent_id, child_id)
                for relationship_id, parent_id, child_id, _ in graph_snapshot
            }
            remove_relationships: list[tuple[int, int, int]] = []
            for relationship_uid in remove_relationship_uids:
                relationship = relationship_by_uid.get(relationship_uid)
                if not relationship:
                    raise ValueError(f"Unknown project relationship: {relationship_uid}")
                remove_relationships.append(relationship)

            affected_ids = {card.id for card in existing_cards.values()}
            affected_ids.update(endpoint for _, parent, child in remove_relationships for endpoint in (parent, child))
            for affected_id in sorted(affected_ids):
                require_card_app_mutation(db, user_or_bot, affected_id, project)

            relationship_type_ids = {
                SnowflakeID.from_short_code(relationship_type_uid) for _, _, relationship_type_uid in add_edges
            }
            relationship_types = self.repo.card_relationship.get_global_relationship_types_map(
                list(relationship_type_ids)
            )
            if len(relationship_types) != len(relationship_type_ids):
                raise ValueError("Unknown relationship type")
            if any(not relation_type.is_active for relation_type in relationship_types.values()):
                raise ValueError("Inactive relationship type cannot be used for a new edge")

            removed_ids = {relationship_id for relationship_id, _, _ in remove_relationships}
            current_edges = {
                (parent_id, child_id)
                for relationship_id, parent_id, child_id, _ in graph_snapshot
                if relationship_id not in removed_ids
            }
            ref_ids = {uid: card.id for uid, card in existing_cards.items()}
            symbolic_edges: set[tuple[str | int, str | int]] = set(current_edges)
            all_types = self.repo.card_relationship.get_global_relationship_types_map(
                list({type_id for _, _, _, type_id in graph_snapshot} | relationship_type_ids)
            )
            blocker_edges = {
                (parent_id, child_id)
                for edge_id, parent_id, child_id, type_id in graph_snapshot
                if edge_id not in removed_ids
                and all_types.get(type_id)
                and all_types[type_id].is_active
                and all_types[type_id].machine_semantic == "blocks"
            }
            for parent_ref, child_ref, type_uid in add_edges:
                parent: str | int = parent_ref if parent_ref in new_refs else ref_ids[parent_ref]
                child: str | int = child_ref if child_ref in new_refs else ref_ids[child_ref]
                if parent == child:
                    raise ValueError("Self relationships are not allowed")
                if (parent, child) in symbolic_edges:
                    raise ValueError("Relationship already exists")
                self._validate_blocking_edge(
                    blocker_edges,
                    parent,
                    child,
                    relationship_types[SnowflakeID.from_short_code(type_uid)].machine_semantic,
                )
                symbolic_edges.add((parent, child))

            anchor_id = anchor_card.id
            if new_refs and not self._all_connected(symbolic_edges, anchor_id, new_refs):
                raise ValueError("Every new card must connect to the anchor card")

            if dry_run:
                return {"new_cards": len(new_cards), "added_edges": len(add_edges), "removed_edges": len(remove_relationships)}

            next_order = self.repo.card.get_next_order(column, {"project_id": project.id}) if new_cards else 0
            creation_anchor = existing_cards[anchor_card.get_uid()]
            cards_to_create = {
                client_ref: Card(
                    visibility=creation_anchor.visibility,
                    owner_user_id=creation_anchor.owner_user_id if creation_anchor.visibility == "PRIVATE" else None,
                    created_by_user_id=user_or_bot.id if isinstance(user_or_bot, User) else None,
                    created_by_bot_id=user_or_bot.id if isinstance(user_or_bot, Bot) else None,
                    project_id=project.id,
                    project_column_id=column.id,
                    title=title,
                    description=EditorContentModel(content=description or ""),
                    order=next_order + index,
                )
                for index, (client_ref, title, description) in enumerate(new_cards)
            }
            converted_edges = [
                (parent_ref, child_ref, SnowflakeID.from_short_code(relationship_type_uid))
                for parent_ref, child_ref, relationship_type_uid in add_edges
            ]
            with execution_readiness_uow() as execution:
                execution.watch([child_id for _, _, child_id in remove_relationships])
                execution.watch(ref_ids[child_ref] for _, child_ref, _ in add_edges if child_ref not in new_refs)
                created_relationships = self.repo.card_relationship.apply_graph_patch(
                    cards_to_create,
                    {uid: card.id for uid, card in existing_cards.items()},
                    converted_edges,
                    list(removed_ids),
                )
                for new_card in cards_to_create.values():
                    execution.watch_new(new_card.id)

        created_cards = []
        for card in cards_to_create.values():
            api_card = card.board_api_response(0, [], [], [])
            created_cards.append(api_card)

        affected_ids = {parent_id for _, parent_id, _ in remove_relationships} | {
            child_id for _, _, child_id in remove_relationships
        }
        affected_ids |= {relationship.card_id_parent for relationship in created_relationships}
        affected_ids |= {relationship.card_id_child for relationship in created_relationships}
        known_card_ids = {card.id for card in existing_cards.values()}
        for card_id in affected_ids - known_card_ids:
            affected_card = InfraHelper.get_by_id_like(Card, card_id)
            if affected_card and affected_card.project_id == project.id:
                existing_cards[affected_card.get_uid()] = affected_card
        affected_cards = [
            card for card in [*existing_cards.values(), *cards_to_create.values()] if card.id in affected_ids
        ]
        if dispatch_effects:
            from .CardService import CardService

            created_snapshots = [
                (card.model_copy(deep=True), api_card)
                for card, api_card in zip(cards_to_create.values(), created_cards, strict=True)
            ]
            affected_snapshots = [
                (card.model_copy(deep=True), self.get_api_list_by_card(card))
                for card in {card.id: card for card in affected_cards}.values()
            ]

            def publish():
                for card, api_card in created_snapshots:
                    CardPublisher.created(project, column, {"card": api_card})
                    CardActivityTask.card_created(user_or_bot, project, card)
                    CardBotTask.card_created(user_or_bot, project, card)
                self._get_service(CardService).publish_work_states(project, [card.id for card, _ in affected_snapshots])
                for card, relationships in affected_snapshots:
                    CardRelationshipPublisher.updated(project, card)
                    CardBotTask.card_relationship_updated(user_or_bot, project, card)

            with DbSession.use(readonly=False) as db:
                db.after_commit(publish)

        return {
            "anchor_card_uid": anchor_card.get_uid(),
            "created_cards": created_cards,
            "created_relationships": [relationship.api_response() for relationship in created_relationships],
            "removed_relationship_uids": remove_relationship_uids,
        }

    @classmethod
    def _validate_blocking_edge(
        cls,
        blocker_edges: set[tuple[str | int, str | int]],
        parent: str | int,
        child: str | int,
        semantic: str | None,
    ) -> None:
        """Only execution prerequisites participate in deadlock detection."""
        if semantic != "blocks":
            return
        if cls._edge_would_create_cycle(blocker_edges, parent, child):
            raise RelationshipCycle("Relationship would create a blocks cycle")
        blocker_edges.add((parent, child))

    @staticmethod
    def _has_path(edges: set[tuple[str | int, str | int]], start: str | int, target: str | int) -> bool:
        """Return whether a directed path already reaches the target."""

        children_by_parent: dict[str | int, set[str | int]] = {}
        for parent, child in edges:
            children_by_parent.setdefault(parent, set()).add(child)
        pending = [start]
        visited: set[str | int] = set()
        while pending:
            node = pending.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            pending.extend(children_by_parent.get(node, ()))
        return False

    @classmethod
    def _edge_would_create_cycle(
        cls,
        edges: set[tuple[str | int, str | int]],
        parent: str | int,
        child: str | int,
    ) -> bool:
        """Reject only cycles closed by the proposed edge, not unrelated legacy cycles."""

        return parent == child or cls._has_path(edges, child, parent)

    @staticmethod
    def _has_cycle(edges: set[tuple[str | int, str | int]]) -> bool:
        """Detect a directed cycle in one linear pass over the graph snapshot."""

        children_by_parent: dict[str | int, set[str | int]] = {}
        indegree: dict[str | int, int] = {}
        for parent, child in edges:
            children_by_parent.setdefault(parent, set()).add(child)
            indegree.setdefault(parent, 0)
            indegree[child] = indegree.get(child, 0) + 1
        pending = [node for node, degree in indegree.items() if degree == 0]
        visited_count = 0
        while pending:
            node = pending.pop()
            visited_count += 1
            for child in children_by_parent.get(node, ()):
                indegree[child] -= 1
                if indegree[child] == 0:
                    pending.append(child)
        return visited_count != len(indegree)

    @staticmethod
    def _all_connected(edges: set[tuple[str | int, str | int]], anchor: str | int, required: set[str]) -> bool:
        """Return whether every new request-local node joins the anchor component."""

        neighbors: dict[str | int, set[str | int]] = {}
        for left, right in edges:
            neighbors.setdefault(left, set()).add(right)
            neighbors.setdefault(right, set()).add(left)
        pending = [anchor]
        visited: set[str | int] = set()
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(neighbors.get(node, ()))
        return required <= visited
