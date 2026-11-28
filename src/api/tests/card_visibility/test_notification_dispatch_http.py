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
    list_notification_inbox_events,
    notification_dispatch_context,
    read_inbox_events,
    set_inbox_event_state,
)
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    CardComment,
    Checkitem,
    Checklist,
    ProjectInvitation,
    ProjectWiki,
    ProjectWikiAssignedUser,
    User,
    UserNotification,
)
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.factory.NotificationService import NotificationService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import UserNotificationRepository
from langboard_shared.publishers.UserPublisher import UserPublisher


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize('credential', ['valid', 'missing', 'invalid', 'expired'])
@pytest.mark.usefixtures("inbox_projection_tables")
def test_dispatch_http_real_internal_token_and_current_acl(current_card, monkeypatch, credential):
    user, project, card, card_service = current_card
    changes = []
    monkeypatch.setattr(UserPublisher, "notification_mutated", lambda recipient, mutation: changes.append((recipient.id, mutation)))
    UserNotification.__table__.create(DbEngine.get_main_engine())
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
        other_user = User(firstname='Other', lastname='Recipient', email='other@example.invalid',
                          password=user.password, activated_at=SafeDateTime.now())
        db.insert(other_user)
        other_note = UserNotification(receiver_id=other_user.id, notifier_type='user', notifier_id=user.id,
            notification_type=NotificationType.MentionedInCard,
            record_list=[('project', project.id), ('card', card.id)])
        db.insert(other_note)
    notification = NotificationService(
        lambda _: card_service, lambda _: None, SimpleNamespace(user_notification=UserNotificationRepository(None, None)),
    )
    service = SimpleNamespace(notification=notification, close=lambda: None)
    monkeypatch.setattr(importlib.import_module('langboard.middlewares.ApiAuthMiddleware'), 'DomainService', lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, 'endpoint', None) in (
            notification_dispatch_context, list_notification_inbox, list_notification_inbox_events,
            read_inbox_events, set_inbox_event_state,
        ):
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
        event_query = {'event_uids': [note.get_uid(), other_note.get_uid()]}
        detail = client.get('/notifications/inbox/events', params=event_query, headers=headers)
        command = {'event_uids': [note.get_uid(), note.get_uid()]}
        if credential != 'valid':
            assert response.status_code in (401, 422)
            assert inbox.status_code in (401, 422)
            assert detail.status_code in (401, 422)
            assert client.put('/notifications/inbox/read-events', headers=headers, json=command).status_code in (401, 422)
            return
        assert response.status_code == 200
        assert inbox.status_code == 200
        assert detail.status_code == 200
        details = detail.json()['notifications']
        assert [event['uid'] for event in details] == [note.get_uid()]
        assert details[0]['records']['card']['uid'] == card.get_uid()
        assert client.get('/notifications/inbox/events', params={'event_uids': [note.get_uid()] * 31}, headers=headers).status_code == 400
        payload = inbox.json()
        assert payload['unread_count'] == 1
        assert [(row['card_uid'], row['latest_event_type'], row['unread_mention']) for row in payload['threads']] == [
            (card.get_uid(), NotificationType.MentionedInCard.value, True),
        ]
        assert payload['threads'][0]['observed_unread_event_uids'] == [note.get_uid()]
        row = payload['threads'][0]
        assert (row['project_title'], row['card_column'], row['card_priority']) == (project.title, 'Current column', 'P2')
        assert (row['actor_name'], row['subscription_reasons']) == ('Fixture Actor', [])
        assert client.get('/notifications/inbox', headers=headers).json() == payload
        filters = {'project_uid': project.get_uid(), 'reason': NotificationType.MentionedInCard.value}
        assert client.get('/notifications/inbox', params=filters, headers=headers).json() == payload
        for params in ({**filters, 'reason': NotificationType.CardMoved.value},
                       {**filters, 'project_uid': card.get_uid()}):
            assert client.get('/notifications/inbox', params=params, headers=headers).json() == {
                'threads': [], 'unread_count': 0, 'next_cursor': None,
            }
        assert client.get('/notifications/inbox?cursor=broken', headers=headers).status_code == 400
        observed = {'event_uids': payload['threads'][0]['observed_unread_event_uids']}
        assert client.put('/notifications/inbox/read-events', headers=headers, json=observed).json() == {'updated_count': 1}
        assert client.put('/notifications/inbox/read-events', headers=headers, json=command).json() == {'updated_count': 0}
        assert changes == [(user.id, {'action': 'inbox_updated'})]
        assert client.get('/notifications/inbox', headers=headers).json()['unread_count'] == 0
        state_change = {
            'event_uids': payload['threads'][0]['observed_state_event_uids'],
            'expected_state': 'inbox', 'state': 'later',
        }
        assert client.put('/notifications/inbox/state', headers=headers, json=state_change).json() == {'updated_count': 1}
        assert client.put('/notifications/inbox/state', headers=headers, json=state_change).json() == {'updated_count': 0}
        assert client.get('/notifications/inbox', headers=headers).json()['threads'] == []
        assert client.get('/notifications/inbox?state=later', headers=headers).json()['unread_count'] == 0
        restore = {**state_change, 'expected_state': 'later', 'state': 'inbox'}
        assert client.put('/notifications/inbox/state', headers=headers, json=restore).json() == {'updated_count': 1}
        with DbSession.atomic() as db:
            for _ in range(250):
                db.insert(UserNotification(
                    receiver_id=user.id, notifier_type='user', notifier_id=user.id,
                    notification_type=NotificationType.CardMoved,
                    record_list=[('project', project.id), ('card', card.id)],
                ))
        large = client.get('/notifications/inbox', headers=headers).json()['threads'][0]
        assert len(large['observed_unread_event_uids']) == 250
        assert len(large['observed_state_event_uids']) == 251
        assert client.put('/notifications/inbox/read-events', headers=headers, json={
            'event_uids': large['observed_unread_event_uids'],
        }).json() == {'updated_count': 250}
        assert client.put('/notifications/inbox/state', headers=headers, json={
            'event_uids': large['observed_state_event_uids'], 'expected_state': 'inbox', 'state': 'later',
        }).json() == {'updated_count': 251}
        assert client.put('/notifications/inbox/state', headers=headers, json={
            'event_uids': large['observed_state_event_uids'], 'expected_state': 'later', 'state': 'inbox',
        }).json() == {'updated_count': 251}
        assert response.json()['allowed'] is True
        assert response.json()['recipient']['email'] == user.email
        with DbSession.use(readonly=False) as db:
            card.visibility = 'INTERNAL'
            db.update(card)
        denied = client.get(f'/notifications/{note.get_uid()}/dispatch-context', headers=headers)
        assert denied.status_code == 200
        assert denied.json() == {'allowed': False, 'recipient': None}
        assert client.get('/notifications/inbox/events', params=event_query, headers=headers).json() == {'notifications': []}
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
