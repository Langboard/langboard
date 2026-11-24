"""Verification persistence rechecks current native visibility under its card lock."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard.routes.board.BoardOrchestrationApi import RecordVerificationEvidenceForm, record_verification_evidence
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, CardVerificationRecord, ProjectAssignedUser, User
from langboard_shared.domain.services.CardVerification import VerificationSubmission
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.publishers import CardPublisher
from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import Session
from test_work_actions import native, native_engine, registered_work, uid  # noqa: F401, F811


def submission():
    return dict(
        expected_change_seq=0,
        decision="partial",
        evidence=[dict(reference="run:1", source_revision="source", environment="isolated")],
    )


def alter(engine, denial, user):
    with Session(engine) as db:
        if denial == "membership":
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == user.id))
        elif denial == "deactivated":
            db.execute(update(User).where(User.id == user.id).values(activated_at=None))
        else:
            changes = {
                "private": dict(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2),
                "deleted": dict(deleted_at=SafeDateTime.now()),
                "archived": dict(archived_at=SafeDateTime.now()),
                "linked": dict(source_type="wiki", source_uid="linked"),
                "foreign": dict(project_id=20),
            }[denial]
            db.execute(update(Card).where(Card.id == 210).values(**changes))
        db.commit()


def assert_no_write(engine):
    with Session(engine) as db:
        assert not db.scalars(select(CardVerificationRecord)).all()
        assert db.get(Card, 210).last_change_seq == 0
    CardPublisher.updated.assert_not_called()


@pytest.mark.parametrize("denial", ["private", "deleted", "archived", "linked", "foreign", "membership", "deactivated"])
@pytest.mark.parametrize("entry", ["direct", "rest"])
def test_native_owner_rejects_current_unreadable_target(registered_work, denial, entry):  # noqa: F811 - imported native fixture
    engine, user, _, boards, *_ = registered_work
    alter(engine, denial, user)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    service = DomainService()
    try:
        if entry == "rest":
            with pytest.raises(ApiException.NotFound_404):
                record_verification_evidence(
                    boards[0].get_uid(),
                    uid(210),
                    RecordVerificationEvidenceForm(**submission()),
                    user_or_bot=user,
                    service=service,
                )
        else:
            assert (
                service.card.record_verification_evidence(
                    user, boards[0], uid(210), VerificationSubmission(**submission())
                )
                is None
            )
        event.remove(engine, "before_cursor_execute", capture)
        assert not any("from card_verification_record" in sql or "from checkitem" in sql for sql in statements)
        assert_no_write(engine)
    finally:
        if event.contains(engine, "before_cursor_execute", capture):
            event.remove(engine, "before_cursor_execute", capture)
        service.close()


@pytest.mark.parametrize("tool", ["work", "record_card_verification_evidence"])
async def test_registered_mcp_rechecks_visibility_after_preflight(registered_work, tool):  # noqa: F811 - imported native fixture
    engine, _, _, boards, _, server, auth = registered_work
    locks = []

    def hide_before_lock(conn, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        query = compiled.statement if compiled is not None else None
        if query is not None and getattr(query, "_for_update_arg", None) is not None and not locks:
            locks.append(statement)
            conn.execute(
                update(Card).where(Card.id == 210).values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
            )

    event.listen(engine, "before_cursor_execute", hide_before_lock)
    token = mcp_auth_context.set(auth)
    try:
        async with Client(server) as client:
            args = dict(project_uid=boards[0].get_uid(), card_uid=uid(210))
            if tool == "work":
                args["change"] = dict(action="record_verification", **submission())
            else:
                args.update(submission())
            with pytest.raises(ToolError, match="unavailable for verification"):
                await client.call_tool(tool, args)
        assert len(locks) == 1
        assert_no_write(engine)
    finally:
        event.remove(engine, "before_cursor_execute", hide_before_lock)
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("channel", [CollaborationChannel.Api, CollaborationChannel.Mcp])
@pytest.mark.parametrize("visibility", ["SHARED", "INTERNAL", "PRIVATE"])
def test_native_owner_retains_allowed_visibility_and_channel(registered_work, channel, visibility):  # noqa: F811 - imported native fixture
    engine, user, _, boards, *_ = registered_work
    with Session(engine) as db:
        db.execute(
            update(Card)
            .where(Card.id == 210)
            .values(
                visibility=visibility,
                owner_user_id=user.id if visibility == "PRIVATE" else None,
                created_by_user_id=user.id,
            )
        )
        db.commit()
    service = DomainService()
    try:
        result = service.card.record_verification_evidence(
            user, boards[0], uid(210), VerificationSubmission(**submission()), channel=channel
        )
        if visibility == "PRIVATE" and channel == CollaborationChannel.Api:
            assert result is None
            assert_no_write(engine)
            return
        assert result["recorded_by_user_uid"] == user.get_uid()
        with Session(engine) as db:
            assert len(db.scalars(select(CardVerificationRecord)).all()) == 1
            assert db.get(Card, 210).last_change_seq == 0
            assert db.get(Card, 210).project_column_id == 110
    finally:
        service.close()


@pytest.mark.parametrize("denial", ["private", "role", "membership", "deactivated", "allowed"])
async def test_authenticated_rest_uses_native_auth_role_and_persistence(registered_work, denial):  # noqa: F811 - imported native fixture
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from jwt import encode
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.domain.models import ProjectRole
    from langboard_shared.Env import Env

    engine, user, _, boards, *_ = registered_work
    if denial not in {"role", "allowed"}:
        alter(engine, denial, user)
    elif denial == "role":
        with Session(engine) as db:
            db.execute(update(ProjectRole).where(ProjectRole.user_id == user.id).values(actions=["read"]))
            db.commit()
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    token = encode(
        dict(
            sub=str(user.id),
            internal="bot",
            api_permission_level="edit",
            exp=int(SafeDateTime.now().timestamp()) + 300,
            iss=Env.PROJECT_NAME,
        ),
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            f"/board/{boards[0].get_uid()}/card/{uid(210)}/verification-evidence",
            json=submission(),
            headers={"X-Api-Token": token},
        )
    expected = {"private": 404, "role": 403, "membership": 404, "deactivated": 401, "allowed": 200}[denial]
    assert response.status_code == expected, response.text
    if denial != "allowed":
        assert "evidence" not in response.text
        assert_no_write(engine)
    else:
        assert response.json()["verification"]["recorded_by_user_uid"] == user.get_uid()


@pytest.mark.parametrize("channel", [CollaborationChannel.Api, CollaborationChannel.Mcp])
@pytest.mark.parametrize("visibility", ["SHARED", "INTERNAL", "PRIVATE"])
def test_native_bot_uses_existing_scope_channel_policy(registered_work, channel, visibility):  # noqa: F811
    from langboard_shared.domain.models import Bot, ProjectBotScope

    engine, _, _, boards, *_ = registered_work
    with Session(engine, expire_on_commit=False) as db:
        bot = Bot(
            id=90,
            name="Bot",
            bot_uname="bot",
            platform="default",
            platform_running_type="default",
            app_api_token="fixture",
        )
        db.add(bot)
        db.flush()
        db.add(ProjectBotScope(id=91, bot_id=bot.id, project_id=10, conditions=[]))
        db.execute(
            update(Card)
            .where(Card.id == 210)
            .values(visibility=visibility, owner_user_id=2 if visibility == "PRIVATE" else None, created_by_user_id=2)
        )
        db.commit()
    service = DomainService()
    try:
        result = service.card.record_verification_evidence(
            bot, boards[0], uid(210), VerificationSubmission(**submission()), channel=channel
        )
        if channel == CollaborationChannel.Mcp and visibility != "PRIVATE":
            assert result["recorded_by_bot_uid"] == bot.get_uid()
            assert result["recorded_by_user_uid"] is None
        else:
            assert result is None
            assert_no_write(engine)
    finally:
        service.close()


def test_unreadable_card_hides_stale_source_and_record_fences(registered_work):  # noqa: F811
    engine, user, _, boards, *_ = registered_work
    alter(engine, "private", user)
    service = DomainService()
    try:
        request = VerificationSubmission(
            **{**submission(), "expected_change_seq": 999, "expected_record_uid": "stale-record"}
        )
        assert service.card.record_verification_evidence(user, boards[0], uid(210), request) is None
        assert_no_write(engine)
    finally:
        service.close()
