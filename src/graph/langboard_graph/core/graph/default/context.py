from typing import Any
from .state import DefaultGraphState


def create_runtime_context_state(tweaks: dict[str, Any]) -> DefaultGraphState:
    variables = _get_variables(tweaks)
    rest_data = variables.get("rest_data")
    rest_data = rest_data if isinstance(rest_data, dict) else {}
    related_cards = rest_data.get("related_cards")
    related_cards = related_cards if isinstance(related_cards, dict) else {}

    parent_cards = _get_related_cards(related_cards, "parents")
    child_cards = _get_related_cards(related_cards, "children")
    relationship_context = [{"direction": "parent", **parent_card} for parent_card in parent_cards] + [
        {"direction": "child", **child_card} for child_card in child_cards
    ]

    return {
        "project_context": _create_project_context(variables, rest_data),
        "card_context": _create_card_context(rest_data),
        "parent_cards": parent_cards,
        "child_cards": child_cards,
        "relationship_context": relationship_context,
        "ontology_context": _get_ontology_context(tweaks, variables, rest_data),
    }


def _get_variables(tweaks: dict[str, Any]) -> dict[str, Any]:
    variables = tweaks.get("LangboardCalledVariablesComponent")
    return variables if isinstance(variables, dict) else {}


def _get_related_cards(related_cards: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = related_cards.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _create_project_context(variables: dict[str, Any], rest_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_uid": variables.get("project_uid") or rest_data.get("project_uid"),
        "event": variables.get("event") or "chat",
        "current_runner_type": variables.get("current_runner_type"),
        "current_runner_data": variables.get("current_runner_data"),
    }


def _create_card_context(rest_data: dict[str, Any]) -> dict[str, Any] | None:
    card_uid = rest_data.get("card_uid")
    if not card_uid:
        return None
    return {
        "card_uid": card_uid,
        "project_uid": rest_data.get("project_uid"),
        "project_column_uid": rest_data.get("project_column_uid"),
    }


def _get_ontology_context(
    tweaks: dict[str, Any],
    variables: dict[str, Any],
    rest_data: dict[str, Any],
) -> dict[str, Any] | None:
    graph_config = tweaks.get("Graph")
    candidates = [
        graph_config.get("ontology_context") if isinstance(graph_config, dict) else None,
        variables.get("ontology_context"),
        rest_data.get("ontology_context"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None
