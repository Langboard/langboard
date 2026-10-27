import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.card_workspace.domain import (  # noqa: E402
    CardDescriptionPatch,
    DescriptionPatchConflict,
    ExactTextReplacement,
    projection_revision,
)
from langboard.card_workspace.infrastructure import native as native_module  # noqa: E402
from langboard.card_workspace.infrastructure.native import (  # noqa: E402
    MAX_NATIVE_SECTION_SOURCE,
    NativeCardWorkspaceAdapter,
)
from langboard.mcp_tools import CardMcp, ProjectMcp  # noqa: E402


class Card:
    """Minimal native card double."""

    id = 7
    project_id = 1
    project_column_id = 2
    created_by_user_id = None
    created_by_bot_id = None

    @staticmethod
    def api_response() -> dict[str, Any]:
        return {"uid": "c1", "title": "Card", "description": "Description"}


def test_project_identity_returns_real_guidance_without_inventing_legacy_descriptions() -> None:
    """Active destinations include their guidance; absent legacy guidance stays empty."""
    project = SimpleNamespace(get_uid=lambda: "p1", title="Workflow", project_type="Other")
    columns = [
        {
            "uid": "doing",
            "name": "Execution",
            "order": 2,
            "description": "Work has started",
            "workflow_stage": "active",
        },
        {"uid": "ready", "name": "Queue", "order": 1},
        {"uid": "archive", "name": "Archive", "order": 0, "is_archive": True, "description": "Hidden"},
    ]
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: project),
        project_column=SimpleNamespace(get_api_list_by_project=lambda _, *, limit: columns[:limit]),
    )
    result = NativeCardWorkspaceAdapter(object(), service).get_project_identity("p1")
    assert result and result["columns"]["items"] == [
        {"uid": "ready", "name": "Queue", "order": 1, "description": "", "workflow_stage": None},
        {
            "uid": "doing",
            "name": "Execution",
            "order": 2,
            "description": "Work has started",
            "workflow_stage": "active",
        },
    ]


def _service(people: list[dict[str, Any]] | None = None) -> tuple[Any, list[tuple[str, int, int | None]]]:
    calls: list[tuple[str, int, int | None]] = []
    project = SimpleNamespace(id=1)
    card = Card()
    column = SimpleNamespace(id=2, project_id=1, name="Backlog", workflow_stage=None)

    def checklists(
        target: Any,
        limit: int,
        checkitems_limit: int,
        *,
        max_checklists: int,
        max_checkitems: int,
        include_work_tracking: bool,
    ) -> list[dict[str, Any]]:
        assert max_checklists == MAX_NATIVE_SECTION_SOURCE
        assert max_checkitems == MAX_NATIVE_SECTION_SOURCE
        assert include_work_tracking is False
        calls.append(("checklists", limit, checkitems_limit))
        return []

    def attachments(target: Any, limit: int) -> list[dict[str, Any]]:
        calls.append(("attachments", limit, None))
        return []

    def metadata(*args: Any, **kwargs: Any) -> dict[str, str]:
        calls.append(("metadata", kwargs["limit"], None))
        return {}

    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda uid: project),
        project_column=SimpleNamespace(get_by_id_like=lambda uid: column, get_workflow_guidance=lambda _: {2: {}}),
        card=SimpleNamespace(
            get_by_id_like=lambda uid: card,
            resolve_readable_card=lambda *args: (project, service.card.get_by_id_like("c1"), object()),
            get_work_states=lambda cards, **kwargs: {item.id: {"version": 1} for item in cards},
            can_delete=lambda actor, target: False,
            is_check_card=lambda target: False,
            _get_completion_checklist=lambda target: None,
            get_api_assigned_user_list=lambda target, limit: people or [],
            get_api_bot_scope_list=lambda target_project, target_card, limit: [],
            get_api_bot_schedule_list=lambda target_project, target_card, limit: [],
        ),
        project_label=SimpleNamespace(get_api_list_by_card=lambda target, limit: []),
        card_relationship=SimpleNamespace(get_api_list_by_card=lambda target, limit, context: []),
        checklist=SimpleNamespace(get_api_list_by_card=checklists),
        card_attachment=SimpleNamespace(get_api_list_by_card=attachments),
        metadata=SimpleNamespace(get_all_as_api=metadata),
    )
    return service, calls


@pytest.mark.parametrize(
    ("user_id", "bot_id", "service_name", "expected_type"),
    [(7, None, "user", "user"), (None, 9, "bot", "bot")],
)
def test_native_source_resolves_the_stored_creator_only(
    user_id: int | None,
    bot_id: int | None,
    service_name: str,
    expected_type: str,
) -> None:
    service, _ = _service()
    card = service.card.get_by_id_like("c1")
    card.created_by_user_id = user_id
    card.created_by_bot_id = bot_id
    calls: list[tuple[str, int]] = []

    def creator(actor_id: int) -> Any:
        calls.append((service_name, actor_id))
        return SimpleNamespace(api_response=lambda: {"uid": "creator", "type": expected_type})

    service.user = SimpleNamespace(get_by_id_like=creator if service_name == "user" else pytest.fail)
    service.bot = SimpleNamespace(get_by_id_like=creator if service_name == "bot" else pytest.fail)

    source = NativeCardWorkspaceAdapter(object(), service).get_card_bundle_source("p1", "c1", frozenset())

    assert source is not None
    assert source.details["creator"] == {"uid": "creator", "type": expected_type}
    assert calls == [(service_name, user_id if user_id is not None else bot_id)]


def test_native_source_fetches_optional_sections_lazily_with_hard_query_limits() -> None:
    """The adapter passes a sentinel hard limit into every potentially large native query."""

    service, calls = _service()
    adapter = NativeCardWorkspaceAdapter(object(), service)

    source = adapter.get_card_bundle_source(
        "p1",
        "c1",
        frozenset({"checklists", "attachments", "metadata"}),
    )

    assert source is not None
    expected_limit = MAX_NATIVE_SECTION_SOURCE + 1
    assert calls == [
        ("checklists", expected_limit, expected_limit),
        ("attachments", expected_limit, None),
        ("metadata", expected_limit, None),
    ]


@pytest.mark.parametrize("checked", [False, True])
def test_bundle_retains_card_checkbox_state_without_exposing_system_checklists(checked: bool) -> None:
    from langboard.card_workspace.application.queries import get_card_bundle
    from langboard.card_workspace.domain import CommentPage, SectionPage

    service, calls = _service()
    service.card.is_check_card = lambda target: True
    service.card._get_completion_checklist = lambda target: SimpleNamespace(is_checked=checked)
    adapter = NativeCardWorkspaceAdapter(object(), service)
    source = adapter.get_card_bundle_source("p1", "c1", frozenset())
    assert source is not None
    result = get_card_bundle(
        SimpleNamespace(get_card_bundle_source=lambda *_: source),
        "p1", "c1", CommentPage(), SectionPage(), include=[],
    ).model_dump()
    assert result["card"]["core"]["is_check_card"] is True
    assert result["card"]["core"]["completed"] is checked
    assert source.checklists == []
    assert calls == []


def test_card_bundle_automation_requires_update_access(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        pass

    class BotActor:
        pass

    monkeypatch.setattr(native_module, "User", Reader)
    monkeypatch.setattr(native_module, "Bot", BotActor)
    service, _ = _service()
    service.project.get_user_role_actions_by_project = Mock(return_value=[])
    service.card.get_api_bot_scope_list = Mock(return_value=[{"uid": "scope"}])
    service.card.get_api_bot_schedule_list = Mock(return_value=[{"uid": "schedule"}])
    adapter = NativeCardWorkspaceAdapter(Reader(), service)
    sections = frozenset({"automation.bot_scopes", "automation.bot_schedules"})

    denied = adapter.get_card_bundle_source("p1", "c1", sections)
    assert denied is not None and denied.bot_scopes == [] and denied.bot_schedules == []
    assert denied.omitted_sections == [
        {"section": section, "reason": "permission_denied"}
        for section in ("automation.bot_scopes", "automation.bot_schedules")
    ]
    service.card.get_api_bot_scope_list.assert_not_called()
    service.card.get_api_bot_schedule_list.assert_not_called()

    service.project.get_user_role_actions_by_project.return_value = [native_module.ProjectRoleAction.Update.value]
    allowed = adapter.get_card_bundle_source("p1", "c1", sections)
    assert allowed is not None
    assert allowed.omitted_sections == []
    assert allowed.bot_scopes == [{"uid": "scope"}]
    assert allowed.bot_schedules == [{"uid": "schedule"}]


def test_native_source_fetches_content_blocks_only_for_the_requested_bundle_section() -> None:
    service, _ = _service()
    blocks = [{"block_uid": "block-1", "order": 0}]
    service.card_content_block = SimpleNamespace(api_blocks_by_card=Mock(return_value=blocks))
    adapter = NativeCardWorkspaceAdapter(object(), service)

    default = adapter.get_card_bundle_source("p1", "c1", frozenset())
    assert default is not None and default.content_blocks == []
    service.card_content_block.api_blocks_by_card.assert_not_called()

    requested = adapter.get_card_bundle_source("p1", "c1", frozenset({"content_blocks"}))
    assert requested is not None and requested.content_blocks == blocks
    service.card_content_block.api_blocks_by_card.assert_called_once()


def test_native_source_rejects_over_bound_people_before_projection() -> None:
    """A native section that exceeds the contract fails instead of entering the projection graph."""

    people = [{"uid": f"u{i}"} for i in range(MAX_NATIVE_SECTION_SOURCE + 1)]
    service, _ = _service(people)
    adapter = NativeCardWorkspaceAdapter(object(), service)

    with pytest.raises(ValueError, match="safe 100-item MCP source bound"):
        adapter.get_card_bundle_source("p1", "c1", frozenset({"people"}))


def test_native_source_projects_linked_wiki_content_without_task_sections() -> None:
    project = SimpleNamespace(id=1)
    card = SimpleNamespace(project_id=1, project_column_id=2, is_linked_resource=True)
    column = SimpleNamespace(id=2, project_id=1, name="Reference")
    actor = object()
    get_details = Mock(
        return_value={
            "uid": "c1",
            "title": "",
            "project_column_uid": "column-1",
            "project_column_name": "Reference",
            "linked_resource": {
                "status": "available",
                "title": "Runbook",
                "content": {"content": "Canonical Wiki body"},
            },
        }
    )
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        project_column=SimpleNamespace(get_by_id_like=lambda _uid: column, get_workflow_guidance=lambda _: {2: {}}),
        card=SimpleNamespace(get_by_id_like=lambda _uid: card, get_details=get_details, resolve_readable_card=lambda *args: (project, card, object())),
    )

    source = NativeCardWorkspaceAdapter(actor, service).get_card_bundle_source(
        "p1",
        "c1",
        frozenset({"description", "people", "checklists", "attachments", "metadata", "automation.bot_scopes"}),
    )

    assert source is not None
    assert source.details["title"] == "Runbook"
    assert source.details["description"] == {"content": "Canonical Wiki body"}
    assert source.checklists == []
    assert source.attachments == []
    assert source.metadata == {}
    assert source.bot_scopes == []
    assert source.bot_schedules == []
    get_details.assert_called_once_with(project, card, actor, channel=native_module.CollaborationChannel.Mcp)


def test_native_checkitem_continuation_reads_only_the_requested_checklist() -> None:
    project = SimpleNamespace(id=1)
    card = SimpleNamespace(id=2, project_id=1, project_column_id=3, api_response=lambda: {"uid": "c1"})
    column = SimpleNamespace(id=3, project_id=1, name="Backlog")
    checklist = SimpleNamespace(id=4, card_id=2, api_response=lambda: {"uid": "cl1", "title": "Checklist"})
    calls: list[tuple[Any, Any, int]] = []
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        project_column=SimpleNamespace(
            get_by_id_like=lambda _uid: column, get_workflow_guidance=lambda _: {column.id: {}}
        ),
        card=SimpleNamespace(
            get_by_id_like=lambda _uid: card,
            resolve_readable_card=lambda *args: (project, card, object()),
            get_work_states=lambda cards, **kwargs: {item.id: {"version": 1} for item in cards},
            can_delete=lambda actor, target: False,
            is_check_card=lambda target: False,
            _get_completion_checklist=lambda target: None,
        ),
        checklist=SimpleNamespace(
            get_by_id_like=lambda _uid: checklist,
            get_api_list_by_card=lambda *_args, **_kwargs: pytest.fail("bulk checklist query used"),
        ),
        checkitem=SimpleNamespace(
            get_api_list_by_checklist=lambda target_card,
            target_checklist,
            limit,
            *,
            max_checkitems,
            include_work_tracking: (
                calls.append((target_card, target_checklist, limit)),
                [{"uid": "ci1", "title": "Item"}],
            )[1]
        ),
    )

    source = NativeCardWorkspaceAdapter(object(), service).get_card_bundle_source(
        "p1", "c1", frozenset({"checkitems:cl1"})
    )

    assert source is not None
    assert source.checklists[0]["checkitems"] == [{"uid": "ci1", "title": "Item"}]
    assert calls == [(card, checklist, MAX_NATIVE_SECTION_SOURCE + 1)]


def test_card_mcp_cardify_reads_back_created_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cardification returns the exact card linked by the source checkitem."""

    calls: list[tuple[Any, ...]] = []
    item = SimpleNamespace(checklist_id=9, cardified_id=None)
    persisted_item = SimpleNamespace(checklist_id=9, cardified_id=None)
    created = SimpleNamespace(
        board_api_response=lambda *_args: {"uid": "created-card", "title": "Promoted task", "private": "hidden"}
    )

    def cardify(*args: Any) -> bool:
        calls.append(args)
        persisted_item.cardified_id = 42
        return True

    project = SimpleNamespace(id=7)
    source_card = SimpleNamespace(id=8, project_id=7, is_linked_resource=False)
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda *_args: (project, source_card))
    service = SimpleNamespace(
        checkitem=SimpleNamespace(cardify=cardify, get_by_id_like=lambda _uid: persisted_item if calls else item),
        checklist=SimpleNamespace(get_by_id_like=lambda _uid: SimpleNamespace(card_id=8)),
        card=SimpleNamespace(get_by_id_like=lambda card_id: created if card_id == 42 else None),
        project_column=SimpleNamespace(get_by_id_like=lambda _uid: SimpleNamespace(project_id=7, is_archive=False)),
    )
    actor = object()
    result = CardMcp.cardify_card_checkitem(" project ", " card ", " item ", " column ", actor, service)

    assert result == {"card": {"uid": "created-card", "title": "Promoted task"}, "source_checkitem_uid": "item"}
    assert calls == [(actor, "project", "card", item, "column")]
    assert item.cardified_id is None


def test_card_mcp_cardify_rejects_column_from_another_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller cannot cardify into a column outside the source project."""

    service = SimpleNamespace(
        project_column=SimpleNamespace(get_by_id_like=lambda _uid: SimpleNamespace(project_id=99, is_archive=False)),
        checkitem=SimpleNamespace(cardify=lambda *_args: pytest.fail("cardify must not run")),
    )
    monkeypatch.setattr(
        CardMcp,
        "_require_task_card",
        lambda *_args: (SimpleNamespace(id=7), SimpleNamespace(id=8, project_id=7, is_linked_resource=False)),
    )
    service.checkitem.get_by_id_like = lambda _uid: SimpleNamespace(checklist_id=9, cardified_id=None)
    service.checklist = SimpleNamespace(get_by_id_like=lambda _uid: SimpleNamespace(card_id=8))

    with pytest.raises(ValueError, match="not active in the source project"):
        CardMcp.cardify_card_checkitem("project", "card", "item", "foreign-column", object(), service)


def test_project_mcp_creation_uses_template_service() -> None:
    """Both project creation tools use the project owner without a card workspace port."""

    class Actor:
        pass

    actor = Actor()
    project = SimpleNamespace(
        id=1,
        title="Operations",
        project_type="Other",
        get_uid=lambda: "project-one",
    )
    names = ["Backlog", "Ready", "In Progress", "Review", "Done"]
    columns = [
        SimpleNamespace(
            api_response=lambda name=name, order=order: {"uid": f"column-{order}", "name": name, "order": order}
        )
        for order, name in enumerate(names)
    ]
    create_project_calls: list[tuple[Any, str, str | None, str, str | None]] = []
    service = SimpleNamespace(
        project_template=SimpleNamespace(
            create_project=lambda *args: (
                create_project_calls.append(args),
                (project, columns, SimpleNamespace(name="SI")),
            )[1],
        ),
    )
    result = CardMcp.provision_project(" Operations ", actor, service, "Room board")
    canonical = ProjectMcp.create_project(" Operations ", "Room board", "Other", actor, service, "SI")

    assert create_project_calls == [
        (actor, "Operations", "Room board", "Other", None, False),
        (actor, "Operations", "Room board", "Other", "SI", False),
    ]
    assert canonical == {"project_uid": "project-one"}
    assert result["project"] == {
        "uid": "project-one",
        "title": "Operations",
        "project_type": "Other",
        "url": "http://localhost:5173/board/project-one",
        "template": "SI",
    }
    assert [column["name"] for column in result["columns"]] == names
    with pytest.raises(ValueError, match="Project title"):
        ProjectMcp.create_project(" ", None, "Other", actor, service)
    with pytest.raises(ValueError, match="Template name"):
        CardMcp.provision_project("Operations", actor, service, template_name=" ")
    assert len(create_project_calls) == 2


def test_project_mcp_creation_propagates_template_failure() -> None:
    """The project owner failure is not hidden by the compatibility alias."""

    class Actor:
        pass

    actor = Actor()
    service = SimpleNamespace(
        project_template=SimpleNamespace(
            create_project=lambda *_args: (_ for _ in ()).throw(RuntimeError("column insert failed"))
        )
    )
    with pytest.raises(RuntimeError, match="column insert failed"):
        CardMcp.provision_project("Operations", actor, service)


def test_card_mcp_creation_selects_server_side_leftmost_active_column() -> None:
    """The canonical tool resolves leftmost server-side and ignores archive order."""

    project = SimpleNamespace(id=1)
    created: list[tuple[Any, ...]] = []
    card = {"uid": "card-one", "title": "First task"}
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_by_id_like=lambda _uid: project,
            get_api_assigned_user_list=lambda _project, where_user_in: [{"uid": "known"}],
        ),
        project_column=SimpleNamespace(
            get_api_list_by_project=lambda _project: [
                {"uid": "done", "name": "Done", "order": 20, "is_archive": False},
                {"uid": "archive", "name": "Archive", "order": -1, "is_archive": True},
                {"uid": "backlog", "name": "Backlog", "order": 10, "is_archive": False},
            ]
        ),
        card=SimpleNamespace(
            create=lambda *args, next_action: (created.append((*args, next_action)), (object(), card))[1]
        ),
    )

    actor = object()
    result = CardMcp.create_card("project-one", "leftmost", " First task ", None, None, actor, service)

    assert created[0][2] == "backlog"
    assert created[0][3] == "First task"
    assert created[0][-1] is None
    assert result == card
    with pytest.raises(ValueError, match="not active"):
        CardMcp.create_card("project-one", "foreign-column", "Unsafe", None, None, actor, service)
    with pytest.raises(ValueError, match="duplicate"):
        CardMcp.create_card("project-one", "leftmost", "Unsafe", None, ["known", "known"], actor, service)
    with pytest.raises(ValueError, match="Unknown project member"):
        CardMcp.create_card("project-one", "leftmost", "Unsafe", None, ["unknown"], actor, service)
    assert len(created) == 1


def test_card_mcp_people_and_labels_validate_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad member or label cannot partially update the card."""

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(id=2, project_id=1, is_linked_resource=False)
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda *_args: (project, card))
    mutations: list[tuple[str, list[str]]] = []
    actor = object()
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_api_assigned_user_list=lambda _project, where_user_in: [{"uid": "member"}],
        ),
        project_label=SimpleNamespace(
            get_api_list_by_project=lambda _project, where_in: [{"uid": "label"}],
            get_api_list_by_card=lambda _card: [{"uid": "label", "name": "Urgent", "secret": "hidden"}],
        ),
        card=SimpleNamespace(
            update_assigned_users=lambda _actor, _project, _card, uids: (
                mutations.append(("members", uids)),
                [SimpleNamespace(get_uid=lambda: "member")],
            )[1],
            update_labels=lambda _actor, _project, _card, uids: mutations.append(("labels", uids)) or True,
        ),
    )

    for people, labels, error in (
        (["member", "member"], ["label"], "duplicates"),
        (["member"], ["missing"], "Unknown label"),
        (["missing"], ["label"], "Unknown project member"),
        (["member"], [" "], "label_uids is required"),
    ):
        with pytest.raises(ValueError, match=error):
            CardMcp.set_card_people_and_labels("project", "card", actor, service, people, labels)
    assert mutations == []

    result = CardMcp.set_card_people_and_labels("project", "card", actor, service, [" member "], [" label "])
    assert result == {"member_uids": ["member"], "labels": [{"uid": "label", "name": "Urgent"}]}
    assert mutations == [("members", ["member"]), ("labels", ["label"])]


def test_native_description_patch_compares_before_updating() -> None:
    """The adapter passes only the locally patched rich-text value to the native service."""

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(project_id=1, description=SimpleNamespace(content="before old after"))
    updates: list[tuple[Any, ...]] = []
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        card=SimpleNamespace(
            get_by_id_like=lambda _uid: card,
            update=lambda *args, **kwargs: (updates.append((*args, kwargs)), {"description": True})[1],
        ),
    )

    result = NativeCardWorkspaceAdapter(object(), service).patch_card_description(
        "project-one",
        "card-one",
        CardDescriptionPatch(
            (ExactTextReplacement(old_text="old", new_text="new"),), projection_revision("before old after")
        ),
    )

    assert result == "before new after"
    assert updates[0][1:3] == (project, card)
    assert updates[0][3]["description"].content == "before new after"
    assert updates[0][4] == {"expected_description": "before old after"}


@pytest.mark.parametrize(("before", "after"), [("", "first body"), ("existing", "")])
def test_native_description_replacement_supports_empty_bodies(before: str, after: str) -> None:
    """A CAS-guarded whole-body write can initialize or clear a description."""

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(project_id=1, description=SimpleNamespace(content=before))
    updates: list[tuple[Any, ...]] = []
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        card=SimpleNamespace(
            get_by_id_like=lambda _uid: card,
            update=lambda *args, **kwargs: (updates.append((*args, kwargs)), {"description": True})[1],
        ),
    )

    result = NativeCardWorkspaceAdapter(object(), service).replace_card_description(
        "project-one", "card-one", after, projection_revision(before)
    )

    assert result == after
    assert updates[0][3]["description"].content == after
    assert updates[0][4] == {"expected_description": before}


def test_native_description_replacement_rejects_stale_revision_before_write() -> None:
    """A whole-body replacement never overwrites content that changed after review."""

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(project_id=1, description=SimpleNamespace(content="current"))
    update = Mock()
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        card=SimpleNamespace(get_by_id_like=lambda _uid: card, update=update),
    )

    with pytest.raises(DescriptionPatchConflict, match="revision does not match"):
        NativeCardWorkspaceAdapter(object(), service).replace_card_description(
            "project-one", "card-one", "replacement", projection_revision("stale")
        )

    update.assert_not_called()


def test_native_description_missing_revision_stops_before_lookup() -> None:
    """A revision-less patch is safely rejected before touching persistence."""
    from langboard.card_workspace.domain import DescriptionPatchConflict

    adapter = NativeCardWorkspaceAdapter(object(), SimpleNamespace())
    with pytest.raises(DescriptionPatchConflict, match="expected_revision is required"):
        adapter.patch_card_description("p", "c", CardDescriptionPatch((ExactTextReplacement("old", "new"),)))


@pytest.mark.parametrize("conflict", [True, False])
def test_native_description_classifies_only_conditional_save_conflicts(conflict: bool) -> None:
    """Only a known pre-commit race is translated; downstream failures remain unknown."""
    from langboard.card_workspace.domain import DescriptionPatchConflict
    from langboard_shared.core.exceptions.CardDescriptionConflict import CardDescriptionConflict

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(project_id=1, description=SimpleNamespace(content="old"))

    def fail(*args: Any, **kwargs: Any) -> None:
        if conflict:
            raise CardDescriptionConflict("concurrent update")
        raise ValueError("post-save effect failed")

    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        card=SimpleNamespace(get_by_id_like=lambda _uid: card, update=fail),
    )
    with pytest.raises(ValueError) as error:
        NativeCardWorkspaceAdapter(object(), service).patch_card_description(
            "p", "c", CardDescriptionPatch((ExactTextReplacement("old", "new"),), projection_revision("old"))
        )
    assert isinstance(error.value, DescriptionPatchConflict) is conflict


def test_native_description_read_revision_can_be_used_for_multi_hunk_patch() -> None:
    """A native editor wrapper must not produce a revision of its JSON envelope."""

    from langboard.card_workspace.application.projections import bounded_text
    from langboard.card_workspace.domain import CardBundleSection
    from langboard_shared.core.db import EditorContentModel

    original = "ALPHA=before\nBETA=keep\nGAMMA=before"
    editor = EditorContentModel(content=original)
    service, _ = _service()
    card = SimpleNamespace(
        id=7,
        project_id=1,
        project_column_id=2,
        description=editor,
        api_response=lambda: {"uid": "c1", "description": editor.model_dump()},
    )
    service.card.get_by_id_like = lambda _uid: card
    updates: list[tuple[Any, ...]] = []
    service.card.update = lambda *args, **kwargs: (updates.append((*args, kwargs)), True)[1]
    adapter = NativeCardWorkspaceAdapter(object(), service)
    source = adapter.get_card_bundle_source("p1", "c1", frozenset({"description"}))
    assert source is not None
    text = bounded_text(source.details["description"], CardBundleSection.CoreDescription)
    assert text.content == original
    assert text.format == "text"

    result = adapter.patch_card_description(
        "p1",
        "c1",
        CardDescriptionPatch(
            (
                ExactTextReplacement(old_text="ALPHA=before", new_text="ALPHA=after"),
                ExactTextReplacement(old_text="GAMMA=before", new_text="GAMMA=after"),
            ),
            expected_revision=text.revision,
        ),
    )
    assert result == "ALPHA=after\nBETA=keep\nGAMMA=after"
    assert len(updates) == 1


def test_native_description_patch_requires_revision_before_lookup() -> None:
    """A direct native caller cannot bypass the gateway's required revision."""

    adapter = NativeCardWorkspaceAdapter(object(), SimpleNamespace())
    with pytest.raises(ValueError, match="expected_revision is required"):
        adapter.patch_card_description("p", "c", CardDescriptionPatch((ExactTextReplacement("old", "new"),)))


@pytest.mark.parametrize("change,accepted", [(False, True), (True, True), (True, False)])
def test_checklist_projection_delegates_only_changed_items_without_per_item_reads(change, accepted) -> None:
    """A 25-item refresh must not reread every ancestor; native mutations retain veto authority."""
    from langboard.card_workspace.domain import ChecklistProjectionItem

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(id=2, project_id=1)
    actual = [{"uid": f"i{i}", "title": f"Item {i}", "is_checked": False} for i in range(25)]
    desired = [ChecklistProjectionItem(f"key{i}", f"Item {i}") for i in range(25)]
    if change:
        desired[0] = ChecklistProjectionItem("key0", "Renamed")
    stored = json.dumps({"version": 1, "checklist_uid": "cl1", "items": {f"key{i}": f"i{i}" for i in range(25)}})
    writes = []
    rename = Mock(return_value=accepted)
    unexpected_read = Mock(side_effect=AssertionError("Redundant per-item native lookup"))
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: project),
        card=SimpleNamespace(get_by_id_like=lambda _: card, resolve_readable_card=lambda *args: (project, card, object())),
        checklist=SimpleNamespace(
            get_api_list_by_card=lambda *args, **kwargs: [{"uid": "cl1", "title": "Projected", "checkitems": actual}],
            get_by_id_like=unexpected_read,
        ),
        checkitem=SimpleNamespace(get_by_id_like=unexpected_read, change_title=rename),
        metadata=SimpleNamespace(
            get_all_as_api=lambda *args, **kwargs: {"projection.checklist.test": stored},
            save=lambda *args: writes.append(args) or True,
        ),
    )
    actor = object()
    adapter = NativeCardWorkspaceAdapter(actor, service)
    if not accepted:
        with pytest.raises(ValueError, match="Checkitem not found in card"):
            adapter.reconcile_card_checklist_projection("p1", "c1", "test", "Projected", desired, None)
        assert writes == []
    else:
        result = adapter.reconcile_card_checklist_projection("p1", "c1", "test", "Projected", desired, None)
        assert result["changed"] is change
        assert json.loads(writes[-1][-1])["receipt"] == result["receipt"]
    unexpected_read.assert_not_called()
    if change:
        rename.assert_called_once_with(actor, "p1", "c1", "i0", "Renamed")
    else:
        rename.assert_not_called()


def test_checklist_projection_deletes_stale_native_item_and_commits_receipt_last() -> None:
    """Removing a projected key must use the still-live native deletion service."""

    project = SimpleNamespace(id=1)
    card = SimpleNamespace(id=2, project_id=1)
    stored = json.dumps({"version": 1, "checklist_uid": "cl1", "items": {"stale": "item1"}})
    deleted: list[tuple[Any, ...]] = []

    def save(_model: Any, _card: Any, _key: str, value: str) -> bool:
        nonlocal stored
        stored = value
        return True

    def delete(*args: Any) -> bool:
        deleted.append(args)
        return True

    def checklists(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"uid": "cl1", "title": "Projected", "checkitems": [] if deleted else [{"uid": "item1"}]}]

    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        card=SimpleNamespace(get_by_id_like=lambda _uid: card, resolve_readable_card=lambda *args: (project, card, object())),
        checklist=SimpleNamespace(get_api_list_by_card=checklists),
        checkitem=SimpleNamespace(delete=delete),
        metadata=SimpleNamespace(
            get_all_as_api=lambda *_args, **_kwargs: {"projection.checklist.test": stored},
            save=save,
        ),
    )
    actor = object()
    result = NativeCardWorkspaceAdapter(actor, service).reconcile_card_checklist_projection(
        "p1", "c1", "test", "Projected", [], expected_receipt=None
    )

    assert deleted == [(actor, "p1", "c1", "item1")]
    assert result["changed"] is True
    assert result["checklist"]["checkitems"] == []
    assert json.loads(stored)["items"] == {}
    assert json.loads(stored)["receipt"] == result["receipt"]


def test_description_repository_rejects_stale_writer_and_preserves_other_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check SQL preservation; the explicit isolated PostgreSQL mode also races two writers."""

    from collections.abc import Iterator
    from contextlib import contextmanager
    from langboard_shared.core.db import DbSession, EditorContentModel
    from langboard_shared.domain.models import Card as NativeCard
    from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.schema import CreateTable

    database_url = os.environ.get("LANGBOARD_DESCRIPTION_TEST_DATABASE_URL", "sqlite://")
    if database_url != "sqlite://":
        from sqlalchemy.engine import make_url

        target = make_url(database_url)
        assert target.host == "127.0.0.1" and target.database == "langboard_description_test"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(CreateTable(NativeCard.__table__, include_foreign_key_constraints=[]))
    with engine.begin() as connection:
        connection.execute(
            NativeCard.__table__.insert().values(
                id=101,
                project_id=1,
                project_column_id=2,
                title="preserved title",
                order=0,
                description=EditorContentModel(content="original"),
                last_change_seq=41,
                last_change_target_type="description",
            )
        )

    @contextmanager
    def isolated_session(readonly: bool) -> Iterator[DbSession]:
        assert not readonly
        with Session(engine, expire_on_commit=False) as session, session.begin():
            yield DbSession(session, readonly=False)

    monkeypatch.setattr(DbSession, "use", isolated_session)
    repository = CardRepository.__new__(CardRepository)
    first = NativeCard(
        id=101,
        project_id=1,
        project_column_id=2,
        title="stale title",
        description=EditorContentModel(content="first edit"),
    )
    second = NativeCard(
        id=101,
        project_id=1,
        project_column_id=2,
        title="other stale title",
        description=EditorContentModel(content="second edit"),
    )
    # Description compare-and-save must preserve the persisted receipt stamp.
    # CardService owns stamping after the repository accepts the write.
    first.last_change_seq = 99
    first.last_change_target_type = "description"
    second.last_change_seq = 42
    assert repository.update_description_if_current(first, "original") is True
    assert repository.update_description_if_current(second, "original") is False
    with engine.connect() as connection:
        row = connection.execute(
            select(
                NativeCard.__table__.c.title, NativeCard.__table__.c.description, NativeCard.__table__.c.last_change_seq
            )
        ).one()
    assert row.title == "preserved title"
    assert row.description.content == "first edit"
    assert row.last_change_seq == 41
    if engine.dialect.name == "postgresql":
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from sqlalchemy import update

        with engine.begin() as connection:
            connection.execute(update(NativeCard.__table__).values(description=EditorContentModel(content="original")))
        barrier = Barrier(2, timeout=10)

        def write(candidate: NativeCard) -> bool:
            barrier.wait()
            return repository.update_description_if_current(candidate, "original")

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(write, (first, second)))
        assert sorted(results) == [False, True]
        with engine.connect() as connection:
            final = connection.execute(select(NativeCard.__table__.c.title, NativeCard.__table__.c.description)).one()
        assert final.title == "preserved title"
        assert final.description.content == ("first edit" if results[0] else "second edit")
    engine.dispose()


@pytest.mark.parametrize("saved", [False, True])
def test_conditional_description_emits_effects_only_after_save(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, saved: bool
) -> None:
    """Rejected writes emit no notifications, activities, bot events or mirror updates."""

    from unittest.mock import Mock
    from langboard_shared.core.db import DbSession, EditorContentModel
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.exceptions.CardDescriptionConflict import CardDescriptionConflict
    from langboard_shared.domain.models import Card as NativeCard
    from langboard_shared.domain.models import CardChangeReceipt, Project
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.helpers import InfraHelper
    from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
    from langboard_shared.publishers import CardPublisher
    from langboard_shared.tasks.activities import CardActivityTask
    from langboard_shared.tasks.bots import CardBotTask
    from sqlalchemy import create_engine

    # Persist both the primary row and the real change receipt before effects.
    engine = create_engine("sqlite://")
    request.addfinalizer(engine.dispose)
    for model in (Project, NativeCard, CardChangeReceipt):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    project = SimpleNamespace(id=1)
    card = NativeCard(
        project_id=1, project_column_id=2, title="Primary", order=0, description=EditorContentModel(content="before")
    )
    destination = Project(owner_id=42, title="Mirror destination")
    with DbSession.use(readonly=False) as db:
        db.insert(card)
        db.insert(destination)
        mirror = NativeCard(
            project_id=destination.id, project_column_id=3, title="",
            source_type=NativeCard.LINKED_RESOURCE_PROJECT_CARD, source_uid=card.get_uid(),
        )
        db.insert(mirror)
    repository = CardRepository.__new__(CardRepository)
    conditional = Mock(wraps=repository.update_description_if_current) if saved else Mock(return_value=False)
    unconditional = Mock()
    service = CardService(
        Mock(),
        Mock(),
        SimpleNamespace(
            card=SimpleNamespace(
                update_description_if_current=conditional,
                update=unconditional,
            )
        ),
    )
    from langboard_shared.domain.services.CardChangeTransaction import dispatch_after_commit

    notifications = Mock()
    # Notification domain writes run before commit; transport is deferred.
    notification_writer = Mock()
    notification_writer.notify_mentioned_in_card.side_effect = lambda *args: dispatch_after_commit(
        notifications.notify_mentioned_in_card, *args
    )
    service._get_service = Mock(return_value=notification_writer)
    service.next_change_seq = Mock(return_value=41)
    service.is_check_card = Mock(return_value=False)
    service.remove_completion_checklist = Mock()
    monkeypatch.setattr(InfraHelper, "get_records_with_foreign_by_params", lambda *_args: (project, card))
    effects = [Mock(), Mock(), Mock(), Mock()]
    metadata_changed = Mock()
    monkeypatch.setattr(CardPublisher, "metadata_changed", metadata_changed)
    monkeypatch.setattr(CardPublisher, "updated", effects[0])
    monkeypatch.setattr(CardActivityTask, "card_updated", effects[1])
    monkeypatch.setattr(CardBotTask, "card_updated", effects[2])
    monkeypatch.setattr(CardPublisher, "linked_resource_changed", effects[3])

    def execute() -> Any:
        return service.update(
            native_module.User.model_construct(id=42), project, card,
            {"description": EditorContentModel(content="after")}, expected_description="before"
        )

    if saved:
        with DbSession.atomic():
            assert execute() is not None
            for effect in effects:
                effect.assert_not_called()
            notifications.notify_mentioned_in_card.assert_not_called()
            metadata_changed.assert_not_called()
            notification_writer.notify_mentioned_in_card.assert_called_once()
        metadata_changed.assert_called_once()
        for effect in effects:
            effect.assert_called_once()
        notifications.notify_mentioned_in_card.assert_called_once()
        assert [row.id for row in effects[3].call_args.args] == [destination.id, mirror.id]
    else:
        with pytest.raises(CardDescriptionConflict, match="concurrent update"):
            execute()
        for effect in effects:
            effect.assert_not_called()
        notifications.notify_mentioned_in_card.assert_not_called()
    from sqlalchemy import select

    with engine.connect() as connection:
        persisted = connection.execute(select(NativeCard.__table__).where(NativeCard.id == card.id)).one()
        receipts = connection.execute(select(CardChangeReceipt.card_id, CardChangeReceipt.change_seq)).all()
    assert persisted.description.content == ("after" if saved else "before")
    assert persisted.last_change_seq == (41 if saved else 0)
    assert receipts == ([(card.id, 41)] if saved else [])
    if not saved:
        metadata_changed.assert_not_called()
        notification_writer.notify_mentioned_in_card.assert_not_called()
    conditional.assert_called_once_with(card, "before")
    unconditional.assert_not_called()


@pytest.mark.parametrize("actor_type", [native_module.User, native_module.Bot])
def test_project_identity_uses_only_server_authenticated_actor(actor_type) -> None:
    actor = actor_type.model_construct(id=42)
    project = SimpleNamespace(get_uid=lambda: "p1", title="Workflow", project_type="Other")
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: project),
        project_column=SimpleNamespace(get_api_list_by_project=lambda _, *, limit: []),
    )
    result = NativeCardWorkspaceAdapter(actor, service).get_project_identity("p1")
    assert result["authenticated_actor"] == {
        "uid": actor.get_uid(),
        "type": "user" if actor_type is native_module.User else "bot",
    }
    assert set(result["authenticated_actor"]) == {"uid", "type"}


def test_project_identity_does_not_trust_actor_shaped_content() -> None:
    actor = SimpleNamespace(get_uid=lambda: "forged-user", type="user", email="private@example.invalid")
    project = SimpleNamespace(get_uid=lambda: "p1", title="Workflow", project_type="Other")
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: project),
        project_column=SimpleNamespace(get_api_list_by_project=lambda _, *, limit: []),
    )
    result = NativeCardWorkspaceAdapter(actor, service).get_project_identity("p1")
    assert result["authenticated_actor"] is None


@pytest.mark.parametrize("actor_type", [native_module.User, native_module.Bot])
@pytest.mark.parametrize("dispatch_effects", [True, False])
@pytest.mark.parametrize("rollback", [False, True])
def test_created_card_projects_authenticated_creator_before_publish(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, actor_type, dispatch_effects: bool, rollback: bool
) -> None:
    """Creation and its event carry the persisted author, never an assignee or private actor data."""
    from contextlib import contextmanager
    from importlib import import_module
    from unittest.mock import Mock
    from langboard_shared.core.db import DbSession, EditorContentModel
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import Card as NativeCard
    from langboard_shared.domain.models import CardChangeReceipt
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.publishers import CardPublisher
    from langboard_shared.tasks.activities import CardActivityTask
    from langboard_shared.tasks.bots import CardBotTask
    from sqlalchemy import create_engine, select

    engine = create_engine("sqlite://")
    request.addfinalizer(engine.dispose)
    for model in (NativeCard, CardChangeReceipt):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    module = import_module(CardService.__module__)
    actor = actor_type.model_construct(id=42)
    monkeypatch.setattr(actor_type, "get_fullname", lambda _: "Creator")
    monkeypatch.setattr(actor_type, "api_response", lambda _: {"avatar": None, "email": "private", "api_key": "secret"})
    project = SimpleNamespace(id=1, owner_id=99)
    column = SimpleNamespace(id=2, is_archive=False, workflow_stage=None)
    monkeypatch.setattr(module.InfraHelper, "get_records_with_foreign_by_params", lambda *_: (project, column))
    committed: list[bool] = []

    @contextmanager
    def uow():
        with DbSession.atomic() as db:
            db.after_commit(lambda: committed.append(True))
            yield SimpleNamespace(watch_new=lambda _: None)

    def insert(card):
        with DbSession.use(readonly=False) as db:
            db.insert(card)

    monkeypatch.setattr(module, "execution_readiness_uow", uow)
    repository = SimpleNamespace(card=SimpleNamespace(insert=insert, get_next_order=lambda *_: 0))
    service = CardService(lambda _: None, lambda _: None, repository)
    service.next_change_seq = Mock(return_value=1)
    # This unit checks creator projection, not primary membership persistence.
    # Use an explicit verified context; creation visibility has its own native tests.
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel

    service.resolve_visibility_context = Mock(return_value=(project, CardVisibilityContext(
        channel=CollaborationChannel.Api, active=True, project_member=True,
        internal_member=True, actor_user_id=42,
    )))
    dispatched: list[dict[str, Any]] = []

    def publish(_project, _column, model):
        assert committed == [True]
        with engine.connect() as connection:
            persisted = connection.execute(select(NativeCard.__table__)).one()
            receipts = connection.execute(select(CardChangeReceipt.card_id, CardChangeReceipt.change_seq)).all()
        assert persisted.created_by_user_id == (42 if actor_type is native_module.User else None)
        assert persisted.created_by_bot_id == (42 if actor_type is native_module.Bot else None)
        assert persisted.last_change_seq == 1
        assert receipts == [(persisted.id, 1)]
        dispatched.append(model)

    monkeypatch.setattr(CardPublisher, "created", publish)
    metadata_changed = Mock()
    activity = Mock()
    bot_event = Mock()
    monkeypatch.setattr(CardPublisher, "metadata_changed", metadata_changed)
    monkeypatch.setattr(CardActivityTask, "card_created", activity)
    monkeypatch.setattr(CardBotTask, "card_created", bot_event)
    card = payload = None

    def create():
        nonlocal card, payload
        with DbSession.atomic():
            card, payload = service.create(
                actor, project, column, "Work", EditorContentModel(content="Body"), dispatch_effects=dispatch_effects
            )
            assert committed == []
            assert dispatched == []
            metadata_changed.assert_not_called()
            activity.assert_not_called()
            bot_event.assert_not_called()
            if rollback:
                raise RuntimeError("Creation rolled back before publish")

    if rollback:
        with pytest.raises(RuntimeError, match="rolled back before publish"):
            create()
    else:
        create()
    with engine.connect() as connection:
        persisted_cards = connection.execute(select(NativeCard.id)).all()
        persisted_receipts = connection.execute(select(CardChangeReceipt.card_id, CardChangeReceipt.change_seq)).all()
    assert persisted_cards == ([] if rollback else [(card.id,)])
    assert persisted_receipts == ([] if rollback else [(card.id, 1)])
    assert committed == ([] if rollback else [True])
    assert metadata_changed.call_count == int(not rollback)
    assert activity.call_count == int(dispatch_effects and not rollback)
    assert bot_event.call_count == int(dispatch_effects and not rollback)
    expected_type = "user" if actor_type is native_module.User else "bot"
    assert card.created_by_user_id == (42 if expected_type == "user" else None)
    assert card.created_by_bot_id == (42 if expected_type == "bot" else None)
    assert payload["member_uids"] == []
    assert payload["creator"] == {
        "uid": actor.get_uid(),
        "type": expected_type,
        "name": "Creator",
        "avatar": None,
        "created_at": card.created_at.isoformat(),
    }
    assert dispatched == ([{"card": payload}] if dispatch_effects and not rollback else [])


def test_project_card_page_resolves_policies_once_for_only_visible_stages():
    rule = {"name": "Stop timers", "is_enabled": True,
            "trigger": {"kind": "stage_entered"}, "condition_match": "all", "conditions": [],
            "actions": [{"kind": "timers.stop", "executor": {"kind": "event_actor"},
                         "config": {}, "requires_previous_success": False}]}
    items = [
        {"uid": "1", "work_state": {"workflow_stage": "released", "completed": True}},
        {"uid": "2", "work_state": {"workflow_stage": "released", "completed": True}},
        {"uid": "3", "work_state": {"workflow_stage": None, "completed": None}},
    ]
    resolve = Mock(
        return_value={
            "released": {
                "key": "released",
                "counts_as_completed": True,
                "confirm_descendant_move": True,
                "rules": [rule],
                "translations": {"ko": {"name": "완료"}},
                "private": "hidden",
            }
        }
    )
    service = SimpleNamespace(
        card=SimpleNamespace(get_api_page_by_project=Mock(return_value=(items, 3, None))),
        workflow_stage=SimpleNamespace(get_api_by_keys=resolve),
    )
    page = NativeCardWorkspaceAdapter(object(), service).get_project_card_page("p", 3, None, None)
    assert service.card.get_api_page_by_project.call_args.kwargs["channel"] == native_module.CollaborationChannel.Mcp
    resolve.assert_called_once_with({"released"})
    assert page.workflow_stages == {
        "released": {"key": "released", "counts_as_completed": True, "confirm_descendant_move": True,
                     "rules": [{**rule, "actions": [{"kind": "timers.stop"}]}]}
    }
    service.card.get_api_page_by_project.return_value = ([], 0, None)
    resolve.reset_mock()
    assert NativeCardWorkspaceAdapter(object(), service).get_project_card_page("p", 3, None, None).workflow_stages == {}
    resolve.assert_not_called()


def test_project_page_resolves_only_visible_column_guidance_once():
    items = [{"uid": str(i), "project_column_uid": "column", "project_column_name": "Doing"} for i in range(20)]
    context = {"column": {"workflow_guidance": "Work now", "workflow_index": "unclassified | Doing"}}
    resolve = Mock(return_value=context)
    service = SimpleNamespace(
        card=SimpleNamespace(get_api_page_by_project=Mock(return_value=(items, 20, None))),
        project_column=SimpleNamespace(get_api_workflow_context=resolve),
    )
    page = NativeCardWorkspaceAdapter(object(), service).get_project_card_page("p", 20, None, None)
    resolve.assert_called_once_with("p", {"column"})
    assert page.columns == context
    service.card.get_api_page_by_project.return_value = ([], 0, None)
    resolve.reset_mock()
    assert NativeCardWorkspaceAdapter(object(), service).get_project_card_page("p", 20, None, None).columns == {}
    resolve.assert_not_called()


def test_native_execute_source_filters_open_items_before_loading_limits():
    service, _ = _service()
    service.checklist.get_api_list_by_card = Mock(return_value=[])
    source = NativeCardWorkspaceAdapter(object(), service).get_card_bundle_source(
        "p1", "c1", frozenset({"checklists", "open_checkitems"})
    )
    assert source is not None
    assert service.checklist.get_api_list_by_card.call_args.kwargs == {
        "limit": MAX_NATIVE_SECTION_SOURCE + 1,
        "checkitems_limit": MAX_NATIVE_SECTION_SOURCE + 1,
        "max_checklists": MAX_NATIVE_SECTION_SOURCE,
        "max_checkitems": MAX_NATIVE_SECTION_SOURCE,
        "include_work_tracking": False,
        "open_only": True,
    }


def test_native_unreadable_source_and_comment_page_do_not_load_sections():
    service, calls = _service()
    service.card.resolve_readable_card = Mock(return_value=None)
    service.card_comment = SimpleNamespace(get_api_page_by_card=Mock(side_effect=AssertionError("hidden comments loaded")))
    adapter = NativeCardWorkspaceAdapter(object(), service)
    assert adapter.get_card_bundle_source("p1", "c1", frozenset({"description", "people", "checklists", "attachments", "metadata"})) is None
    assert calls == []
    with pytest.raises(ValueError, match="Card not found"):
        adapter.get_comment_page("c1", 5, None, None)
    service.card_comment.get_api_page_by_card.assert_not_called()


def test_bundle_resolves_current_visibility_without_redundant_replica_reads():
    service, _ = _service()
    actor = object()
    current = service.card.resolve_readable_card("p1", "c1", actor)
    service.card.resolve_readable_card = Mock(return_value=current)
    service.project.get_by_id_like = Mock(side_effect=AssertionError("redundant project read"))
    service.card.get_by_id_like = Mock(side_effect=AssertionError("redundant card read"))
    source = NativeCardWorkspaceAdapter(actor, service).get_card_bundle_source("p1", "c1", frozenset())
    assert source is not None
    service.card.resolve_readable_card.assert_called_once_with("p1", "c1", actor, native_module.CollaborationChannel.Mcp)
