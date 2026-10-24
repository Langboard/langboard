"""Persist source-resolved Decision rounds with current native authority and real DB fences."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4
import pytest
import test_decision_actors as actor_tests
from langboard.routes.board.forms.Decision import CreateDecisionForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain import models as m
from langboard_shared.domain.services.DecisionActors import DecisionActors
from langboard_shared.domain.services.DecisionFlow import DecisionConflict, DecisionDenied, DecisionMissing
from pydantic import ValidationError
from sqlalchemy import delete, inspect, select, update
from test_decision_actors import grants, preview  # noqa: F401
from test_decision_flow import form
from test_decision_hitl import bound as hitl_bound  # noqa: F401
from test_decision_hitl import policy_native  # noqa: F401


actor_native = actor_tests.native


@pytest.fixture
def native(actor_native):
    engine = actor_native[0]
    m.User.metadata.create_all(
        engine, tables=[m.DecisionCase.__table__, m.DecisionOption.__table__, m.DecisionResponse.__table__]
    )
    return actor_native


def payload(f, *, kind="proposal", sources=None, resolved=None, **changes):
    sources = sources or [{"kind": "group", "uid": f[6].get_uid()}, {"kind": "team", "uid": f[8].get_uid()}]
    resolved = resolved or preview(f, sources, kind=kind)
    actor_uids = resolved["actor_uids"]
    data = dict(
        request_id=str(uuid4()),
        source_kind=kind,
        source_ref=f[5].get_uid(),
        question="Current selected actors",
        target_revision=f[1].flow.list(f[2][0], f[3].get_uid(), f[5].get_uid())["target_revision"],
        participant_uids=actor_uids,
        actor_sources=sources,
        expected_actor_snapshot=resolved["snapshot"],
        policy={"op": "all", "required": 0, "children": [{"actor_uid": uid} for uid in actor_uids]}
        if kind == "proposal"
        else None,
    )
    if kind == "poll":
        data.update(
            deadline=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            poll=dict(
                options=[dict(key="a", label="A"), dict(key="b", label="B")],
                participation_quorum=1,
                participation_policy="vote_only",
                winner_policy="plurality",
                tie_policy="needs_info",
                no_quorum_policy="expired",
                no_winner_policy="needs_info",
                needs_info_policy="record",
                allow_change=True,
                tally_visibility="after_close",
            ),
        )
    data.update(changes)
    return CreateDecisionForm(**data)


def create(f, data):
    return f[1].flow.create(f[2][0], f[3].get_uid(), f[5].get_uid(), data)


def listing(f, actor=0):
    return f[1].flow.list(f[2][actor], f[3].get_uid(), f[5].get_uid())["items"]


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_persisted_overlap_deduped_provenance_and_no_grants(native, kind):
    f = native
    sources = [
        {"kind": "group", "uid": f[6].get_uid()},
        {"kind": "group", "uid": f[7].get_uid()},
        {"kind": "team", "uid": f[8].get_uid()},
        {"kind": "user", "uid": f[2][1].get_uid()},
    ]
    resolved = preview(f, sources, kind=kind)
    data = payload(f, kind=kind, sources=sources, resolved=resolved)
    before = grants(f)
    case = create(f, data)
    assert case["participant_uids"] == resolved["actor_uids"] and len(case["participant_uids"]) == 2
    assert case["actor_snapshot"] == resolved
    assert len(case["actor_snapshot"]["actors"][0]["sources"]) == 4
    assert case["actor_snapshot"]["snapshot_version"] == 1 and case["effect_result"] is None
    assert grants(f) == before
    with f[0].connect() as db:
        persisted = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        assert persisted["actor_snapshot"] == resolved
        assert persisted["snapshot"]["participant_uids"] == resolved["actor_uids"]
    # Other readers receive no Group identifiers, labels or counts through provenance.
    assert listing(f, 1)[0]["actor_snapshot"] is None
    # Membership changes never rewrite the old snapshot or re-expand same-UUID replay.
    with f[0].begin() as db:
        db.execute(
            delete(m.ScimGroupMember).where(
                m.ScimGroupMember.group_id == f[6].id, m.ScimGroupMember.user_id == f[2][2].id
            )
        )
    replay = create(f, data)
    assert replay["replayed"] and replay["actor_snapshot"] == resolved
    assert replay["participant_uids"] == case["participant_uids"]


@pytest.mark.parametrize("fact", ["membership", "policy", "settings", "principal"])
def test_stale_preview_facts_require_reconfirmation_without_write(native, fact):
    f = native
    data = payload(f)
    with f[0].begin() as db:
        if fact == "membership":
            db.execute(
                delete(m.BoardTeamMember).where(
                    m.BoardTeamMember.team_id == f[8].id, m.BoardTeamMember.user_id == f[2][2].id
                )
            )
        elif fact == "policy":
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == f[2][2].id).values(actions=[]))
        elif fact == "settings":
            db.execute(
                update(m.SettingRole)
                .where(m.SettingRole.user_id == f[2][0].id)
                .values(actions=["group_read", "group_manage"])
            )
        else:
            db.execute(update(m.User).where(m.User.id == f[2][2].id).values(activated_at=None))
    with pytest.raises(DecisionConflict, match="preview"):
        create(f, data)
    assert listing(f) == []
    fresh = payload(f)
    assert create(f, fresh)["actor_snapshot"]["snapshot"] == fresh.expected_actor_snapshot


@pytest.mark.parametrize("revoke", ["group_read", "creator_update", "creator_disabled", "tenant"])
def test_current_authority_precedes_create_uuid_replay(native, revoke):
    f = native
    data = payload(f)
    create(f, data)
    with f[0].begin() as db:
        if revoke == "group_read":
            db.execute(update(m.SettingRole).where(m.SettingRole.user_id == f[2][0].id).values(actions=[]))
        elif revoke == "creator_update":
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == f[2][0].id).values(actions=["read"]))
        elif revoke == "creator_disabled":
            db.execute(update(m.User).where(m.User.id == f[2][0].id).values(activated_at=None))
        else:
            db.execute(update(m.Organization).values(is_active=False))
    with pytest.raises((DecisionDenied, DecisionMissing)):
        create(f, data)
    if revoke == "group_read":
        assert listing(f)[0]["actor_snapshot"] is None


def test_source_shape_current_private_and_foreign_boundaries(native):
    f = native
    data = payload(f)
    with pytest.raises(ValidationError):
        CreateDecisionForm(**{**data.model_dump(mode="json"), "expected_actor_snapshot": None})
    with pytest.raises(ValidationError):
        CreateDecisionForm(
            **{**data.model_dump(mode="json"), "actor_sources": [{"kind": "role", "uid": f[6].get_uid()}]}
        )
    for actors in (
        list(reversed(data.participant_uids)),
        data.participant_uids[:1],
        data.participant_uids + [f[2][3].get_uid()],
    ):
        with pytest.raises(DecisionConflict):
            create(f, data.model_copy(update={"participant_uids": actors}))
    foreign = data.model_copy(update={"actor_sources": [type(data.actor_sources[0])(kind="team", uid=f[9].get_uid())]})
    with pytest.raises(DecisionDenied):
        create(f, foreign)
    with f[0].begin() as db:
        db.execute(update(m.Card).values(visibility="PRIVATE", owner_user_id=f[2][2].id, created_by_user_id=f[2][2].id))
    with pytest.raises((DecisionDenied, DecisionMissing)):
        create(f, data)
    with f[0].connect() as db:
        assert db.execute(select(m.DecisionCase)).all() == []


def test_uuid_payload_collision_and_concurrent_create_replay(native):
    f = native
    data = payload(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(f, data), range(2)))
    assert sorted(item["replayed"] for item in results) == [False, True]
    assert results[0]["actor_snapshot"] == results[1]["actor_snapshot"]
    with pytest.raises(DecisionConflict):
        create(f, data.model_copy(update={"question": "Changed"}))
    assert len(listing(f)) == 1


def test_source_membership_write_cannot_cross_creation_snapshot_fence(native, monkeypatch):
    f = native
    data = payload(f)
    resolved, release, writing, written = Event(), Event(), Event(), Event()
    original = DecisionActors.resolve_in_transaction

    def held(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert DbSession.has_active_transaction()
        resolved.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(DecisionActors, "resolve_in_transaction", held)

    def mutate():
        writing.set()
        with f[0].begin() as db:
            db.execute(
                delete(m.ScimGroupMember).where(
                    m.ScimGroupMember.group_id == f[6].id, m.ScimGroupMember.user_id == f[2][2].id
                )
            )
        written.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        creating = pool.submit(create, f, data)
        assert resolved.wait(5)
        mutation = pool.submit(mutate)
        assert writing.wait(5)
        try:
            assert not written.wait(0.1)
        finally:
            release.set()
        case = creating.result(timeout=5)
        mutation.result(timeout=5)
    assert case["actor_snapshot"]["snapshot"] == data.expected_actor_snapshot
    assert listing(f)[0]["actor_snapshot"] == case["actor_snapshot"]


def test_response_provenance_replay_current_group_visibility(native):
    f = native
    sources = [{"kind": "group", "uid": f[6].get_uid()}, {"kind": "user", "uid": f[2][0].get_uid()}]
    data = payload(f, sources=sources)
    case = create(f, data)
    command = form(case)
    receipt = f[1].flow.respond(f[2][0], f[3].get_uid(), case["uid"], command)
    assert receipt["actor_snapshot"] == case["actor_snapshot"]
    with f[0].begin() as db:
        db.execute(update(m.SettingRole).where(m.SettingRole.user_id == f[2][0].id).values(actions=[]))
    replay = f[1].flow.respond(f[2][0], f[3].get_uid(), case["uid"], command)
    assert replay["replayed"] and replay["actor_snapshot"] is None
    with f[0].connect() as db:
        assert db.execute(select(m.DecisionResponse.receipt)).scalar_one() == receipt


def test_actor_column_migration_legacy_replay_parity_and_used_guard(native):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from test_decision_flow import create as create_concrete

    f = native
    flow_fixture = (f[0], f[1].flow, f[2], f[3], f[4], f[5])
    concrete = create_concrete(flow_fixture)
    command = form(concrete)
    receipt = f[1].flow.respond(f[2][1], f[3].get_uid(), concrete["uid"], command)
    assert "actor_snapshot" not in concrete
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024090000-5def678a9012.py"
    spec = importlib.util.spec_from_file_location("actor_create_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "4abc567def90"
    with f[0].begin() as db:
        assert db.execute(select(m.DecisionCase.actor_snapshot)).scalar_one() is None
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
            assert "actor_snapshot" not in {item["name"] for item in inspect(db).get_columns("decision_case")}
            migration.upgrade()
    assert f[1].flow.respond(f[2][1], f[3].get_uid(), concrete["uid"], command) == {**receipt, "replayed": True}
    assert "actor_snapshot" not in listing(f)[0]
    data = payload(f)
    create(f, data)
    with pytest.raises(RuntimeError, match="retained Decision actor"):
        with f[0].begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                migration.downgrade()


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_authenticated_preview_create_stale_reconfirm_and_current_visibility(native, monkeypatch, tmp_path, kind):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardDecisionActorsApi, BoardDecisionApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    f = native
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "actor-creation-http-key-at-least-32-bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    base = f"/board/{f[3].get_uid()}/card/{f[5].get_uid()}"
    sources = [{"kind": "group", "uid": f[6].get_uid()}, {"kind": "team", "uid": f[8].get_uid()}]
    with TestClient(app) as client:
        assert client.get(base + "/decisions").status_code == 401

        def login(index):
            access, refresh = AuthSecurity.authenticate(f[2][index].id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            return {"Authorization": f"Bearer {access}"}

        manager = login(0)
        discovered = client.get(base + "/decisions", headers=manager)
        assert discovered.status_code == 200 and discovered.json()["items"] == []
        resolved = client.post(
            base + "/decision-actors/preview", headers=manager, json={"source_kind": kind, "sources": sources}
        )
        assert resolved.status_code == 200, resolved.text
        data = payload(f, kind=kind, sources=sources, resolved=resolved.json()).model_dump(mode="json")
        data["target_revision"] = discovered.json()["target_revision"]
        assert (
            client.post(
                base + "/decisions", headers=manager, json={**data, "expected_actor_snapshot": None}
            ).status_code
            == 400
        )
        forged = {**data, "participant_uids": [*data["participant_uids"], f[2][3].get_uid()]}
        assert client.post(base + "/decisions", headers=manager, json=forged).status_code == 409
        foreign = {**data, "actor_sources": [{"kind": "team", "uid": f[9].get_uid()}]}
        denied = client.post(base + "/decisions", headers=manager, json=foreign)
        assert denied.status_code == 403 and f[9].name not in denied.text
        created = client.post(base + "/decisions", headers=manager, json=data)
        assert created.status_code == 200, created.text
        assert created.headers["cache-control"] == "no-store"
        case = created.json()
        assert case["actor_snapshot"] == resolved.json()
        with f[0].begin() as db:
            db.execute(
                delete(m.ScimGroupMember).where(
                    m.ScimGroupMember.group_id == f[6].id, m.ScimGroupMember.user_id == f[2][2].id
                )
            )
        assert (
            client.post(base + "/decisions", headers=manager, json={**data, "request_id": str(uuid4())}).status_code
            == 409
        )
        replay = client.post(base + "/decisions", headers=manager, json=data)
        assert (
            replay.status_code == 200
            and replay.json()["replayed"]
            and replay.json()["actor_snapshot"] == resolved.json()
        )
        participant = login(1)
        assert (
            client.post(base + "/decisions", headers=participant, json={**data, "request_id": str(uuid4())}).status_code
            == 403
        )
        assert client.get(base + "/decisions", headers=participant).json()["items"][0]["actor_snapshot"] is None
        command = (
            form(case, choice="vote", option_uid=case["poll"]["options"][0]["uid"]) if kind == "poll" else form(case)
        )
        response_url = f"/board/{f[3].get_uid()}/decisions/{case['uid']}/responses"
        body = command.model_dump(mode="json")
        assert (
            client.post(response_url, headers=participant, json={**body, "actor_uid": f[2][0].get_uid()}).status_code
            == 400
        )
        response = client.post(response_url, headers=participant, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["actor_snapshot"] is None
        with f[0].begin() as db:
            db.execute(update(m.SettingRole).where(m.SettingRole.user_id == f[2][0].id).values(actions=[]))
        manager = login(0)
        assert client.post(base + "/decisions", headers=manager, json=data).status_code == 403
        assert client.get(base + "/decisions", headers=manager).json()["items"][0]["actor_snapshot"] is None


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_source_departure_blocks_submission_replay_and_existing_vote_count(native, kind):
    f = native
    sources = [{"kind": "group", "uid": f[6].get_uid()}]
    data = payload(f, kind=kind, sources=sources)
    case = create(f, data)
    command = form(case, choice="vote", option_uid=case["poll"]["options"][0]["uid"]) if kind == "poll" else form(case)
    first = f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)
    with f[0].begin() as db:
        db.execute(delete(m.ScimGroupMember).where(m.ScimGroupMember.group_id == f[6].id))
    for actor, body in [(1, command), (2, form(first, choice="abstain"))]:
        with pytest.raises(DecisionDenied, match="membership"):
            f[1].flow.respond(f[2][actor], f[3].get_uid(), case["uid"], body)
    current = listing(f)[0]
    if kind == "poll":
        # Manager has hidden tally; authorized participant's former ballot cannot reveal it.
        assert listing(f, 1)[0]["poll"]["my_response"] is None
        with DbSession.atomic() as db:
            from langboard_shared.core.db import SqlBuilder

            row = db.exec(SqlBuilder.select.table(m.DecisionCase)).first()
            result = f[1].flow._poll_data(
                db,
                row,
                f[5],
                CollaborationChannel.Api,
                datetime.now(timezone.utc),
            )[3]
            assert result.vote_count == 0
    else:
        assert current["state"] == "pending" and current["active_responders"] == []
    assert current["actor_snapshot"] == case["actor_snapshot"]


@pytest.mark.parametrize("surviving", ["team", "user", "none"])
def test_deleted_source_overlap_user_selector_and_native_revoke(native, surviving):
    f = native
    sources = [{"kind": "group", "uid": f[6].get_uid()}]
    if surviving == "team":
        sources.append({"kind": "team", "uid": f[8].get_uid()})
    elif surviving == "user":
        sources.append({"kind": "user", "uid": f[2][1].get_uid()})
    case = create(f, payload(f, sources=sources))
    with f[0].begin() as db:
        db.execute(delete(m.ScimGroup).where(m.ScimGroup.id == f[6].id))
    command = form(case)
    if surviving == "none":
        with pytest.raises(DecisionDenied):
            f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)
    else:
        receipt = f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)
        assert receipt["state"] == "pending"
        with f[0].begin() as db:
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == f[2][1].id).values(actions=[]))
        with pytest.raises(DecisionDenied):
            f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)


def test_deleted_team_and_new_member_never_replace_frozen_actors(native):
    f = native
    case = create(f, payload(f, sources=[{"kind": "team", "uid": f[8].get_uid()}]))
    with DbSession.atomic() as db:
        db.insert(m.BoardTeamMember(team_id=f[8].id, user_id=f[2][0].id))
    with pytest.raises(DecisionDenied, match="frozen"):
        f[1].flow.respond(f[2][0], f[3].get_uid(), case["uid"], form(case))
    with f[0].begin() as db:
        db.execute(delete(m.BoardTeam).where(m.BoardTeam.id == f[8].id))
    with pytest.raises(DecisionDenied, match="membership"):
        f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], form(case))
    assert listing(f)[0]["participant_uids"] == case["participant_uids"]


@pytest.mark.parametrize("kind", ["proposal", "poll"])
@pytest.mark.parametrize("explicit_nulls", [False, True])
def test_legacy_concrete_request_hash_unchanged_and_replay_without_sources(native, kind, explicit_nulls):
    from langboard_shared.domain.services.DecisionFlow import _hash

    f = native
    data = payload(f, kind=kind)
    # Freeze the pre-actor/HITL contract independently of new model defaults.
    legacy_fields = {
        "request_id",
        "source_kind",
        "source_ref",
        "question",
        "target_revision",
        "participant_uids",
        "policy",
        "deadline",
        "poll",
    }
    raw = data.model_dump(mode="json", include=legacy_fields)
    legacy = dict(raw)
    if kind == "proposal":
        legacy.pop("poll", None)
    expected = _hash({"project_uid": f[3].get_uid(), "card_uid": f[5].get_uid(), **legacy})
    if explicit_nulls:
        raw.update(actor_sources=None, expected_actor_snapshot=None, approval_uid=None)
    concrete = CreateDecisionForm(**raw)
    case = create(f, concrete)
    with f[0].connect() as db:
        row = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        assert row["request_hash"] == expected and row["actor_snapshot"] is None
    assert "actor_snapshot" not in case
    # Replay must also accept an already persisted hash from the old contract.
    with f[0].begin() as db:
        db.execute(update(m.DecisionCase).values(request_hash=expected))
    assert create(f, concrete) == {**case, "replayed": True}
    source_form = data.model_copy(update={"request_id": concrete.request_id})
    with pytest.raises(DecisionConflict, match="payload mismatch"):
        create(f, source_form)


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_actor_source_request_hash_retains_explicit_selection(native, kind):
    from langboard_shared.domain.services.DecisionFlow import _hash

    f = native
    data = payload(f, kind=kind)
    case = create(f, data)
    hashed = data.model_dump(mode="json", exclude={"approval_uid"})
    if kind == "proposal":
        hashed.pop("poll")
    expected = _hash({"project_uid": f[3].get_uid(), "card_uid": f[5].get_uid(), **hashed})
    with f[0].connect() as db:
        assert db.execute(select(m.DecisionCase.request_hash)).scalar_one() == expected
    assert create(f, data) == {**case, "replayed": True}
    with pytest.raises(DecisionConflict, match="payload mismatch"):
        create(f, data.model_copy(update={"actor_sources": None, "expected_actor_snapshot": None}))


def test_hitl_request_hash_retains_explicit_native_binding(hitl_bound):  # noqa: F811
    from langboard_shared.domain.services.DecisionFlow import _hash
    from test_decision_flow import create as create_policy

    f, _, _, approval = hitl_bound
    users, board, card = f[2], f[3], f[5]
    data = dict(source_kind="graph_approval", approval_uid=approval.get_uid())
    case = create_policy(f, **data)
    # The persisted native binding remains part of the request identity.
    with f[0].connect() as db:
        row = db.execute(select(m.DecisionCase.__table__)).mappings().one()
    raw = dict(
        request_id=row["request_id"],
        source_kind="graph_approval",
        source_ref=card.get_uid(),
        approval_uid=approval.get_uid(),
        question=row["question"],
        target_revision=row["target_revision"],
        participant_uids=row["snapshot"]["participant_uids"],
        policy=row["snapshot"]["policy"],
        deadline=None,
    )
    assert row["request_hash"] == _hash({"project_uid": board.get_uid(), "card_uid": card.get_uid(), **raw})
    replay = f[1].create(users[0], board.get_uid(), card.get_uid(), CreateDecisionForm(**raw))
    assert replay == {**case, "replayed": True}
    with pytest.raises(DecisionConflict, match="payload mismatch"):
        f[1].create(
            users[0], board.get_uid(), card.get_uid(),
            CreateDecisionForm(**{**raw, "approval_uid": users[1].get_uid()}),
        )


def test_terminal_source_proposal_membership_departure_cancels_current_passage(native):
    f = native
    sources = [{"kind": "group", "uid": f[7].get_uid()}]  # Exactly one currently eligible actor.
    case = create(f, payload(f, sources=sources))
    command = form(case)
    receipt = f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)
    assert receipt["state"] == "passed"
    with f[0].begin() as db:
        db.execute(
            delete(m.ScimGroupMember).where(
                m.ScimGroupMember.group_id == f[7].id, m.ScimGroupMember.user_id == f[2][1].id
            )
        )
    current = listing(f)[0]
    assert current["state"] == "cancelled" and current["blocked_reason"] == "terminal_authority_changed"
    assert current["allowed_actions"] == []
    with pytest.raises(DecisionDenied, match="membership"):
        f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], command)
    with f[0].connect() as db:
        assert db.execute(select(m.DecisionResponse.receipt)).scalar_one() == receipt


def test_source_created_response_and_count_require_current_active_tenant(native):
    f = native
    case = create(f, payload(f, sources=[{"kind": "team", "uid": f[8].get_uid()}]))
    with f[0].begin() as db:
        db.execute(update(m.Organization).values(is_active=False))
    with pytest.raises(DecisionDenied, match="membership"):
        f[1].flow.respond(f[2][1], f[3].get_uid(), case["uid"], form(case))
    assert listing(f)[0]["active_responders"] == []
