"""App installation owns one endpoint; human mutation cannot bypass ownership."""

import unittest
from unittest.mock import patch
import test_notification_destination_native as fixtures
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security import KeyVault
from langboard_shared.domain.models import WebhookSetting
from langboard_shared.domain.models.AppEventDestinationTemplate import AppEventDestinationTemplate
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppManagedWebhook import require_unmanaged_webhooks, webhook_responses
from langboard_shared.domain.services.AppRegistry import (
    AppRegistryConflict,
    AppRegistryDenied,
    ensure_definition_webhook,
)


class ManagedWebhookNativeTest(unittest.TestCase):
    def setUp(self):
        fixtures.NotificationDestinationNativeTest.setUp(self)
        for model in (WebhookSetting, AppEventDestinationTemplate):
            model.__table__.create(self.engine)
        self.event = "io.langboard.notification.requested.v1"
        with DbSession.atomic() as db:
            self.definition.declaration = {
                **self.definition.declaration,
                "trust": {
                    "authentication_origins": ["https://identity.example"],
                    "data_origins": ["https://approved.example"],
                    "mcp_origins": [],
                },
                "event_webhook": {"url": "https://approved.example/events", "events": [self.event]},
            }
            db.update(self.definition)
        self.revision = self.definition.edit_revision()
        self.publisher = patch("langboard_shared.domain.services.AppManagedWebhook.AppSettingPublisher")
        self.publisher.start()
        self.addCleanup(self.publisher.stop)

    def ensure(self, actor=None, revision=None):
        return ensure_definition_webhook(actor or self.other, self.definition.key, revision or self.revision)

    def test_fresh_install_repeated_reconcile_preserves_endpoint_and_key(self):
        with patch.object(KeyVault, "create_key", return_value="one-time-secret") as create:
            first = self.ensure()
            second = self.ensure()
        create.assert_called_once()
        self.assertEqual(first["signing_secret"], "one-time-secret")
        self.assertNotIn("signing_secret", second)
        self.assertEqual(first["uid"], second["uid"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertFalse(second["changed"])
        with DbSession.atomic() as db:
            settings = db.exec(SqlBuilder.select.table(WebhookSetting)).all()
            self.assertEqual(len(settings), 1)
            with self.assertRaises(AppGovernanceDenied):
                require_unmanaged_webhooks(db, [settings[0].id])
        response = webhook_responses(settings)
        self.assertEqual(response[0]["owner_apps"][0]["key"], self.definition.key)
        self.assertNotIn("one-time-secret", str(response))

    def test_legacy_adoption_preserves_signing_reference_and_denies_mixed_bulk(self):
        with DbSession.atomic() as db:
            legacy = WebhookSetting(
                name="Legacy", url="https://approved.example/events", events=[self.event], secret_id="existing-key"
            )
            manual = WebhookSetting(
                name="Manual", url="https://approved.example/manual", events=[self.event], secret_id="manual-key"
            )
            db.insert(legacy)
            db.insert(manual)
            db.insert(
                AppEventDestinationTemplate(
                    app_key=self.definition.key, webhook_id=legacy.id, target_revision="old", trust_revision="old"
                )
            )
        with patch.object(KeyVault, "create_key") as create:
            result = self.ensure()
        create.assert_not_called()
        self.assertEqual(result["uid"], legacy.get_uid())
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(WebhookSetting).where(WebhookSetting.id == legacy.id)).first()
            self.assertEqual(row.secret_id, "existing-key")
            require_unmanaged_webhooks(db, [manual.id])
            with self.assertRaises(AppGovernanceDenied):
                require_unmanaged_webhooks(db, [manual.id, legacy.id])

    def test_current_admin_and_revision_required_before_secret_creation(self):
        with patch.object(KeyVault, "create_key") as create:
            with self.assertRaises(AppRegistryDenied):
                self.ensure(actor=self.owner)
            with self.assertRaises(AppRegistryConflict):
                self.ensure(revision="0" * 64)
            with DbSession.atomic() as db:
                self.other.is_admin = False
                db.update(self.other)
            with self.assertRaises(AppRegistryDenied):
                self.ensure()
        create.assert_not_called()

    def test_authenticated_install_and_reconcile_expose_key_once_with_no_store(self):
        import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env

        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        access, refresh = AuthSecurity.authenticate(self.other.id)
        with TestClient(app) as client, patch.object(KeyVault, "create_key", return_value="one-time-secret") as create:
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            response = client.post(
                f"/settings/apps/registry/{self.definition.key}/event-webhook",
                json={"expected_revision": self.revision},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["webhook"]["signing_secret"], "one-time-secret")
            again = client.post(
                f"/settings/apps/registry/{self.definition.key}/event-webhook",
                json={"expected_revision": self.revision},
                headers=headers,
            )
            self.assertEqual(again.status_code, 200, again.text)
            self.assertNotIn("signing_secret", again.json()["webhook"])
            with DbSession.atomic() as db:
                self.other.is_admin = False
                db.update(self.other)
            denied = client.post(
                f"/settings/apps/registry/{self.definition.key}/event-webhook",
                json={"expected_revision": self.revision},
                headers=headers,
            )
            self.assertEqual(denied.status_code, 403, denied.text)
        create.assert_called_once()

    def test_normal_service_mutations_cannot_modify_or_delete_an_owned_endpoint(self):
        from langboard_shared.domain.services import DomainService

        with patch.object(KeyVault, "create_key", return_value="one-time-secret"):
            receipt = self.ensure()
        service = DomainService()
        self.addCleanup(service.close)
        for operation in (
            lambda: service.app_setting.update_webhook_setting(receipt["uid"], name="Hijacked"),
            lambda: service.app_setting.update_webhook_setting(receipt["uid"]),
            lambda: service.app_setting.delete_webhook_setting(receipt["uid"]),
            lambda: service.app_setting.delete_selected_webhook_settings([receipt["uid"]]),
        ):
            with self.assertRaises(AppGovernanceDenied):
                operation()
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(WebhookSetting)).first()
            self.assertEqual(row.name, self.definition.declaration["name"])

    def test_registry_approval_itself_registers_endpoint_without_separate_manual_action(self):
        from langboard_shared.domain.services.AppRegistry import save_definition

        declaration = {**self.definition.declaration, "key": "independent-receiver"}
        with patch.object(KeyVault, "create_key", return_value="install-only-key") as create:
            receipt = save_definition(self.other, declaration)
        create.assert_called_once()
        self.assertEqual(receipt["event_webhook"]["app_key"], declaration["key"])
        self.assertEqual(receipt["event_webhook"]["signing_secret"], "install-only-key")

    def test_collaborative_draft_cannot_bypass_app_ownership_even_for_admin(self):
        from langboard.routes.auth.SocketAuthorization import is_editor_document_write_authorized
        from langboard_shared.core.routing import SettingSocketTopicID, SocketTopic

        with patch.object(KeyVault, "create_key", return_value="one-time-secret"):
            receipt = self.ensure()
        self.assertFalse(
            is_editor_document_write_authorized(
                None,
                self.other,
                (SocketTopic.AppSettings, SettingSocketTopicID.Webhook.value),
                f"app-settings:{receipt['uid']}:webhook",
            )
        )

    def test_removing_declaration_withdraws_delivery_without_deleting_key(self):
        from langboard_shared.domain.models import BoardAppBinding
        from langboard_shared.domain.services.AppRegistry import save_definition

        BoardAppBinding.__table__.create(self.engine)
        with patch.object(KeyVault, "create_key", return_value="one-time-secret"):
            receipt = self.ensure()
        declaration = {key: value for key, value in self.definition.declaration.items() if key != "event_webhook"}
        declaration["version"] = "1.0.1"
        with patch.object(KeyVault, "delete_key") as delete:
            save_definition(self.other, declaration, self.revision)
        delete.assert_not_called()
        with DbSession.atomic() as db:
            setting = db.exec(SqlBuilder.select.table(WebhookSetting)).first()
            template = db.exec(SqlBuilder.select.table(AppEventDestinationTemplate)).first()
            self.assertFalse(setting.is_enabled)
            self.assertFalse(template.is_enabled)
            self.assertEqual(setting.get_uid(), receipt["uid"])
            with self.assertRaises(AppGovernanceDenied):
                require_unmanaged_webhooks(db, [setting.id])

    def test_declared_callback_cannot_be_rebound_through_legacy_template_api(self):
        from langboard_shared.domain.services.AppEventDestinationTemplates import bind_app_service_template

        with patch.object(KeyVault, "create_key", return_value="one-time-secret"):
            self.ensure()
        with DbSession.atomic() as db:
            setting = db.exec(SqlBuilder.select.table(WebhookSetting)).first()
        with self.assertRaises(AppGovernanceDenied):
            bind_app_service_template(self.other, self.definition.key, setting.id, expected_revision=1)

    def test_failed_registration_rolls_back_endpoint_and_removes_new_vault_key(self):
        with (
            patch.object(KeyVault, "create_key", return_value="one-time-secret") as create,
            patch.object(KeyVault, "delete_key") as delete,
            patch(
                "langboard_shared.domain.services.AppManagedWebhook._target_revision",
                side_effect=RuntimeError("rollback"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                self.ensure()
        delete.assert_called_once_with(create.call_args.args[0])
        with DbSession.atomic() as db:
            self.assertEqual(db.exec(SqlBuilder.select.table(WebhookSetting)).all(), [])
            self.assertEqual(db.exec(SqlBuilder.select.table(AppEventDestinationTemplate)).all(), [])

    def test_enclosing_transaction_and_savepoint_failure_reclaim_new_key(self):
        for savepoint in (False, True):
            with (
                self.subTest(savepoint=savepoint),
                patch.object(KeyVault, "create_key", return_value="one-time-secret") as create,
                patch.object(KeyVault, "delete_key") as delete,
            ):
                if savepoint:
                    with DbSession.atomic() as db:
                        with self.assertRaises(RuntimeError):
                            with db.savepoint():
                                self.ensure()
                                raise RuntimeError("outer failure")
                else:
                    with self.assertRaises(RuntimeError), DbSession.atomic():
                        self.ensure()
                        raise RuntimeError("outer failure")
                delete.assert_called_once_with(create.call_args.args[0])
                with DbSession.atomic() as db:
                    self.assertEqual(db.exec(SqlBuilder.select.table(WebhookSetting)).all(), [])
