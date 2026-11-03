"""Native migrated compaction storage fixtures shared by persistence and API tests."""

import importlib.util
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.ai.comment_compaction import CommentCompactionSnapshot, CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardComment,
    CardMetadata,
    InternalBot,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.infrastructure.repositories.factory.CardCommentCompactionRepository import (
    CardCommentCompactionRepository,
)
from tests.model_providers.workflow_rule_fixtures import create_rule_tables


@pytest.fixture
def compactions(board):
    _, actor, project, _, _, columns, *_ = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardComment, CardMetadata, InternalBot, WorkflowStageDefinition, WorkflowEffectRun):
        model.__table__.create(engine, checkfirst=True)
    create_rule_tables()
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261021020000-c4a8f16d3092.py"
    spec = importlib.util.spec_from_file_location("compaction_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        principal_path = path.parent / "20261022010000-b4a719de028c.py"
        principal_spec = importlib.util.spec_from_file_location("compaction_principal_migration", principal_path)
        principal = importlib.util.module_from_spec(principal_spec)
        principal_spec.loader.exec_module(principal)
        with Operations.context(MigrationContext.configure(connection)):
            principal.upgrade()
    with DbSession.atomic() as db:
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Original card")
        binding = InternalBot(bot_type=InternalBotType.CommentCompaction, display_name="Fixture",
                              platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
                              value='{"model_name":"fixture"}', api_key="private-fixture-credential")
        db.insert(card)
        db.insert(binding)
        comment = CardComment(card_id=card.id, user_id=actor.id,
                              content=EditorContentModel(content="Original private comment"))
        db.insert(comment)
    snapshot = CommentCompactionSnapshot(sources=[{
        "evidence_ref": f"comment:{comment.get_uid()}", "created_at": comment.created_at,
        "updated_at": comment.updated_at, "text": comment.content.content,
    }])
    draft = CompactionDraft(claims=[{"text": "Reported work remains unverified", "kind": "unresolved",
                                    "evidence_refs": [snapshot.sources[0].evidence_ref]}])
    return CardCommentCompactionRepository(None, None), actor, card, binding, comment, snapshot, draft, migration
