"""Reviewed onboarding discovery and exact retained request reconciliation."""
import json
import unittest
from test_notification_destination_native import NotificationDestinationNativeTest
from test_notification_delivery_native import NotificationDeliveryNativeTest
from langboard_shared.core.db import DbSession
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppNotificationOverview import read_notification_onboarding, read_connection_notification_settings

class NotificationOnboardingNativeTest(unittest.TestCase):
    setUp = NotificationDestinationNativeTest.setUp
    connection_revision = NotificationDestinationNativeTest.connection_revision
    challenge = NotificationDestinationNativeTest.challenge

    def declare(self, entry=None, origins=None, enabled=True):
        with DbSession.atomic() as db:
            declaration = dict(self.definition.declaration)
            if entry is not None:
                declaration["notification_onboarding"] = {"url": entry}
            if origins is not None:
                declaration["trust"] = {**declaration["trust"], "data_origins": origins}
            self.definition.declaration = declaration
            self.definition.is_enabled = enabled
            db.update(self.definition)

    def test_no_entry_has_no_authentication_origin_fallback(self):
        result = read_notification_onboarding(self.owner, self.connection.id)
        self.assertFalse(result["available"])
        self.assertIsNone(result["entry"])
        self.assertEqual(result["connection_revision"], self.connection_revision())
        with self.assertRaises(AppGovernanceDenied):
            read_notification_onboarding(self.other, self.connection.id)

    def test_fixed_reviewed_entry_changes_identity_and_disabled_app_is_denied(self):
        original = self.connection_revision()
        self.declare("https://receiver.example/notifications/connect", ["https://receiver.example"])
        result = read_notification_onboarding(self.owner, self.connection.id)
        self.assertTrue(result["available"])
        self.assertEqual(result["entry"], {"url": "https://receiver.example/notifications/connect", "origin": "https://receiver.example", "protocol_version": 1})
        self.assertNotEqual(result["connection_revision"], original)
        self.assertEqual(result["app_revision"], self.definition.edit_revision())
        self.declare(enabled=False)
        with self.assertRaises(AppGovernanceDenied):read_notification_onboarding(self.owner, self.connection.id)

    def test_authentication_origin_alone_cannot_authorize_entry(self):
        self.declare("https://identity.example/notifications/connect")
        with self.assertRaises(ValueError):read_notification_onboarding(self.owner, self.connection.id)

    def test_original_challenge_request_lookup_is_exact_and_secret_free(self):
        first = self.challenge("original-operation")
        self.challenge("different-operation")
        page = read_connection_notification_settings(self.owner, self.connection.id, section="challenges", request_id="original-operation")
        self.assertEqual([row["challenge_uid"] for row in page["items"]], [first["challenge_uid"]])
        self.assertNotIn(first["challenge_token"], json.dumps(page))
        self.assertEqual(read_connection_notification_settings(self.owner, self.connection.id, section="challenges", request_id="missing")["items"], [])
        with self.assertRaises(AppGovernanceDenied):read_connection_notification_settings(self.other, self.connection.id, section="challenges", request_id="original-operation")
        with self.assertRaises(ValueError):read_connection_notification_settings(self.owner, self.connection.id, request_id="original-operation")

    def test_native_onboarding_http_requires_owner_session_and_never_accepts_app_token(self):
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppNotificationDeliveryApi
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity, KeyVault
        from langboard_shared.Env import Env
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        path = f"/settings/apps/connections/{self.connection.get_uid()}/notification-onboarding"
        with patch.object(KeyVault, "get_key", return_value="notification-test-signing-key-" * 4), TestClient(app) as client:
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers={"Authorization": "Bearer " + self.token["token"]}).status_code, 401)
            for user, expected in [(self.owner, 200), (self.other, 403)]:
                access, refresh = AuthSecurity.authenticate(user.id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                response = client.get(path, headers={"Authorization": "Bearer " + access})
                self.assertEqual(response.status_code, expected, response.text)
                if expected == 200:
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertFalse(response.json()["available"])
