"""Exercise the dispatch route through the real socket-token authentication path."""
import importlib
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import encode
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.notification.NotificationApi import (
    list_notification_inbox,
    notification_dispatch_context,
    read_inbox_events,
)
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    CardComment,
    Checkitem,
    Checklist,
    ProjectInvitation,
    ProjectWiki,
    ProjectWikiAssignedUser,
    UserNotification,
)
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.factory.NotificationService import NotificationService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import UserNotificationRepository


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize('credential', ['valid', 'missing', 'invalid', 'expired'])
def test_dispatch_http_real_internal_token_and_current_acl(current_card, monkeypatch, credential):
    user, project, card, card_service = current_card
    UserNotification.__table__.create(DbEngine.get_main_engine())
    Bot.__table__.create(DbEngine.get_main_engine())
    for model in (CardComment, Checklist, Checkitem, ProjectInvitation, ProjectWiki, ProjectWikiAssignedUser):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        card.visibility = 'SHARED'
        card.owner_user_id = None
        db.update(card)
        note = UserNotification(receiver_id=user.id, notifier_type='user', notifier_id=user.id,
            notification_type=NotificationType.MentionedInCard,
            record_list=[('project', project.id), ('card', card.id)])
        db.insert(note)
    notification = NotificationService(
        lambda _: card_service, lambda _: None, SimpleNamespace(user_notification=UserNotificationRepository(None, None)),
    )
    service = SimpleNamespace(notification=notification, close=lambda: None)
    monkeypatch.setattr(importlib.import_module('langboard.middlewares.ApiAuthMiddleware'), 'DomainService', lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, 'endpoint', None) in (notification_dispatch_context, list_notification_inbox, read_inbox_events):
            for dep in route.dependant.dependencies:
                if dep.name == 'service':
                    app.dependency_overrides[dep.call] = lambda: service
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    token = encode({'sub': str(user.id), 'internal': 'bot', 'api_permission_level': 'read',
        'exp': int(SafeDateTime.now().timestamp()) + (300 if credential != 'expired' else -300),
        'iss': Env.PROJECT_NAME}, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
    headers = {} if credential == 'missing' else {'X-Api-Token': token if credential != 'invalid' else 'invalid'}
    with TestClient(app) as client:
        response = client.get(f'/notifications/{note.get_uid()}/dispatch-context', headers=headers)
        inbox = client.get('/notifications/inbox?limit=1', headers=headers)
        command = {'event_uids': [note.get_uid(), note.get_uid()]}
        if credential != 'valid':
            assert response.status_code in (401, 422)
            assert inbox.status_code in (401, 422)
            assert client.put('/notifications/inbox/read-events', headers=headers, json=command).status_code in (401, 422)
            return
        assert response.status_code == 200
        assert inbox.status_code == 200
        payload = inbox.json()
        assert payload['unread_count'] == 1
        assert [(row['card_uid'], row['latest_event_type'], row['unread_mention']) for row in payload['threads']] == [
            (card.get_uid(), NotificationType.MentionedInCard.value, True),
        ]
        assert payload['threads'][0]['observed_unread_event_uids'] == [note.get_uid()]
        assert payload['threads'][0]['has_more_unread_events'] is False
        assert client.get('/notifications/inbox', headers=headers).json() == payload
        assert client.get('/notifications/inbox?cursor=broken', headers=headers).status_code == 400
        observed = {'event_uids': payload['threads'][0]['observed_unread_event_uids']}
        assert client.put('/notifications/inbox/read-events', headers=headers, json=observed).json() == {'updated_count': 1}
        assert client.put('/notifications/inbox/read-events', headers=headers, json=command).json() == {'updated_count': 0}
        assert client.get('/notifications/inbox', headers=headers).json()['unread_count'] == 0
        assert response.json()['allowed'] is True
        assert response.json()['recipient']['email'] == user.email
        with DbSession.use(readonly=False) as db:
            card.visibility = 'INTERNAL'
            db.update(card)
        denied = client.get(f'/notifications/{note.get_uid()}/dispatch-context', headers=headers)
        assert denied.status_code == 200
        assert denied.json() == {'allowed': False, 'recipient': None}
        assert client.get('/notifications/inbox', headers=headers).json() == {
            'threads': [], 'unread_count': 0, 'next_cursor': None,
        }

        with DbSession.use(readonly=False) as db:
            card.visibility = "SHARED"
            db.update(card)
            user.activated_at = None
            db.update(user)
        inactive = client.get(f"/notifications/{note.get_uid()}/dispatch-context", headers=headers)
        # Current account revocation is rejected by authentication before dispatch.
        assert inactive.status_code == 401
        assert inactive.json() == {}
