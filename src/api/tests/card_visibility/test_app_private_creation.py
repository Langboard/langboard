# ruff: noqa: F811
"""Native app creation must succeed with immutable PRIVATE migration installed."""

import importlib.util
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from src.api.tests.apps.test_app_card_creation import (
    call,
    creation,  # noqa: F401
)
from src.api.tests.apps.test_app_card_creation import (
    test_current_scope_rechecked_before_replay as check_current_scope,
)
from src.api.tests.apps.test_app_card_creation import (
    test_effect_failure_retains_one_committed_card_and_no_implicit_retry as check_effect_failure,
)
from src.api.tests.apps.test_app_card_creation import (
    test_http_create_replay_conflict_and_authentication as check_http_authentication,
)
from src.api.tests.apps.test_app_card_creation import (
    test_native_backlog_presentation_and_replay as check_native_receipt,
)
from src.api.tests.apps.test_app_card_creation import (
    test_presentation_failure_rolls_back_card_and_source_receipt as check_rollback,
)


@pytest.fixture
def migrated_creation(creation):
    engine = DbEngine.get_main_engine()
    assert engine.dialect.name == "postgresql"
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261007230000-3d5a71e8c942.py"
    spec = importlib.util.spec_from_file_location("app_private_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        # Reconstruct the pre-migration columns on this empty ORM fixture, then
        # execute the real upgrade, including its constraints and SQL trigger.
        connection.execute(text("ALTER TABLE card DROP COLUMN owner_user_id"))
        connection.execute(
            text("ALTER TABLE card ADD CONSTRAINT card_visibility CHECK (visibility IN ('INTERNAL','SHARED'))")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='card'::regclass AND tgname='card_private_immutable'"
                )
            ).scalar_one()
            == 1
        )
    return creation


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_solo_owner_app_creation_with_private_trigger(migrated_creation):
    check_native_receipt(migrated_creation)
    actor = migrated_creation[0][1]
    with DbSession.use(readonly=False) as db:
        card = db.exec(SqlBuilder.select.table(Card)).first()
        assert (card.visibility, card.owner_user_id, card.created_by_user_id, card.created_by_bot_id) == (
            "INTERNAL",
            None,
            actor.id,
            None,
        )


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_user_default_and_direct_sql_private_boundaries(migrated_creation):
    board = migrated_creation[0]
    service = DomainService()
    try:
        assert service.card.default_creation_visibility(board[2], board[1]) == CardVisibility.Private
        private, _ = service.card.create(board[1], board[2], board[5][0], "Personal vault", dispatch_effects=False)
    finally:
        service.close()
    assert private.visibility == "PRIVATE" and private.owner_user_id == board[1].id
    app_uid = call(migrated_creation)["card_uid"]
    with DbEngine.get_main_engine().begin() as connection:
        app_id = connection.execute(text("SELECT id FROM card WHERE visibility='INTERNAL'")).scalar_one()
        assert app_uid != private.get_uid()
        for statement, card_id in (
            ("visibility='INTERNAL',owner_user_id=NULL", private.id),
            ("visibility='SHARED',owner_user_id=NULL", private.id),
            ("owner_user_id=2,created_by_user_id=2", private.id),
            ("created_by_user_id=2", private.id),
            ("created_by_bot_id=2", private.id),
            ("visibility='PRIVATE',owner_user_id=1", app_id),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(f"UPDATE card SET {statement} WHERE id=:id"), {"id": card_id})
        assert connection.execute(
            text("SELECT visibility,owner_user_id FROM card WHERE id=:id"), {"id": private.id}
        ).one() == ("PRIVATE", board[1].id)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revoke", ["resource", "grant", "definition", "column", "shared_personal", "lifecycle"])
def test_migrated_replay_requires_current_grants(migrated_creation, revoke):
    check_current_scope(migrated_creation, revoke)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_migrated_native_creation_rollback(migrated_creation, monkeypatch):
    check_rollback(migrated_creation, monkeypatch)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_migrated_http_authentication(migrated_creation):
    check_http_authentication(migrated_creation)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_migrated_effect_failure_retains_receipt(migrated_creation, monkeypatch):
    check_effect_failure(migrated_creation, monkeypatch)


@pytest.mark.parametrize("internal", [True, False])
def test_app_creation_preserves_member_classification(current_card, monkeypatch, internal):
    from langboard_shared.Env import Env

    actor, project, _, service = current_card
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(service, "_resolve_internal_access", lambda _: internal)
    assert service.default_creation_visibility(project, actor) == CardVisibility.Private
    assert service.default_creation_visibility(project, actor, app_owned=True) == (
        CardVisibility.Internal if internal else CardVisibility.Shared
    )
