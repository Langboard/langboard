# ruff: noqa: F811
"""Current scoped content reads retain native revision and bounded pagination."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppCardReadApi  # noqa: F401
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_execution_grant import grant_scope


@pytest.mark.parametrize('board', ['sqlite-http', 'postgresql-test'], indirect=True)
def test_card_read_native_revision_paging_and_current_authority(board):
    connection, definition, binding, _, card, token = grant_scope(board)
    with DbSession.atomic() as db:
        definition.declaration = {**definition.declaration, 'capabilities': [*definition.declaration['capabilities'], 'cards.read']}
        binding.granted_capabilities = [*binding.granted_capabilities, 'cards.read']
        card.description = EditorContentModel(content='abcdefghij')
        card.last_change_seq = 47
        db.update(definition)
        db.update(binding)
        db.update(card)
    app = FastAPI()
    app.include_router(AppRouter.api)
    path = f'/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}'
    headers = {'Authorization': f'Bearer {token}'}
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer invalid'}).status_code == 401
        first = client.get(path, headers=headers, params={'offset': 0, 'limit': 4})
        assert first.status_code == 200, first.text
        value = first.json()
        assert value['card_revision'] == 47 and value['execution_generation'] == 3
        assert value['description'] == {'content': 'abcd', 'format': 'markdown', 'total_chars': 10, 'next_offset': 4}
        assert first.headers['cache-control'] == 'no-store'
        assert client.get(path, headers=headers, params={'offset': 4, 'limit': 6}).json()['description']['content'] == 'efghij'
        assert client.get(path, headers=headers, params={'offset': 100, 'limit': 4}).json()['description']['next_offset'] is None
        for capability in ('cards.read', 'resources.read'):
            with DbSession.atomic() as db:
                saved = list(binding.granted_capabilities)
                binding.granted_capabilities = [x for x in saved if x != capability]
                db.update(binding)
            assert client.get(path, headers=headers).status_code == 403
            with DbSession.atomic() as db:
                binding.granted_capabilities = saved
                db.update(binding)
        with DbSession.atomic() as db:
            connection.state = 'disconnected'
            db.update(connection)
        assert client.get(path, headers=headers).status_code in (401, 403)
