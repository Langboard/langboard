"""One admin service binding, independently owned personal connection pins."""

import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch
from test_credential_custody_native import CredentialCustodyNativeTest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SnowflakeID, SafeDateTime
from langboard_shared.domain.models import AppConnection, AppEventDestination, WebhookSetting, User
from langboard_shared.domain.models.AppEventDestinationTemplate import AppEventDestinationTemplate
from langboard_shared.domain.services.AppConnectionAuthentication import authenticate_connection_credential
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, AppGovernanceConflict
from langboard_shared.domain.services.AppEventDestination import current_destination, bind_app_event_destination
from langboard_shared.domain.services.AppEventDestinationTemplates import (
    bind_app_service_template, disable_app_service_template, prepare_connection_service_destination,
    read_connection_service_template, read_app_service_template,
)
import langboard_shared.domain.services.AppCredentialCustody as custody


class ServiceTemplateNativeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CredentialCustodyNativeTest.setUp(self)
        for model in (AppEventDestination, WebhookSetting, AppEventDestinationTemplate):
            model.__table__.create(self.engine)
        real = patch.object(custody, "current_destination", new=current_destination)
        real.start()
        self.addCleanup(real.stop)
        with DbSession.atomic() as db:
            declaration = self.definition.declaration
            self.definition.declaration = {**declaration, "trust": {
                **declaration["trust"], "data_origins": ["https://approved.example"]}}
            db.update(self.definition)
            self.setting = WebhookSetting(name="One approved service", url="https://approved.example/events",
                secret_id="test-signing-ref", events=[custody.HANDOFF_EVENT])
            db.insert(self.setting)
        self.template = bind_app_service_template(self.other, self.connection.app_key, self.setting.id)

    async def test_admin_read_retains_repair_fence_and_never_exposes_signing_configuration(self):
        with self.assertRaises(AppGovernanceDenied):
            read_app_service_template(self.owner, self.connection.app_key)
        read = read_app_service_template(self.other, self.connection.app_key)
        self.assertEqual(read["template"]["revision"], 1)
        self.assertTrue(read["current"])
        self.assertNotIn("test-signing-ref", str(read))
        self.assertNotIn("/events", str(read))
        disable_app_service_template(self.other, self.connection.app_key, expected_revision=1)
        disabled = read_app_service_template(self.other, self.connection.app_key)
        self.assertEqual(disabled["template"]["revision"], 2)
        self.assertFalse(disabled["current"])
        self.assertFalse(disabled["template"]["enabled"])
        with self.assertRaises(AppGovernanceDenied):
            read_app_service_template(self.other, "missing-app")

    def connection_revision(self):
        return CredentialCustodyNativeTest.connection_revision(self)

    async def test_nonadmin_owners_reuse_one_service_without_secret_or_custody_copy(self):
        self.assertFalse(self.owner.is_admin)
        read = read_connection_service_template(self.owner, self.connection.id)
        self.assertEqual(read["template"]["origin"], "https://approved.example")
        self.assertNotIn("test-signing-ref", str(read))
        first = prepare_connection_service_destination(self.owner, self.connection.id, expected_template_revision=1)
        with DbSession.atomic() as db:
            user = User(firstname="Second", lastname="Owner", email="second@example.invalid",
                        password="test", activated_at=SafeDateTime.now())
            db.insert(user)
            connection = AppConnection(app_key=self.connection.app_key, owner_id=user.id, state="connected")
            db.insert(connection)
        second = prepare_connection_service_destination(user, connection.id, expected_template_revision=1)
        self.assertNotEqual(first["destination_uid"], second["destination_uid"])
        with DbSession.atomic() as db:
            rows = db.exec(SqlBuilder.select.table(AppEventDestination)).all()
            self.assertEqual({row.webhook_id for row in rows}, {self.setting.id})
            self.assertEqual(len(db.exec(SqlBuilder.select.table(WebhookSetting)).all()), 1)
            self.assertEqual(len(db.exec(SqlBuilder.select.table(AppEventDestinationTemplate)).all()), 1)
            # Preparing configuration does not create another custody consent.
            from langboard_shared.domain.models.AppConnectionCredentialCustody import AppConnectionCredentialCustody
            self.assertEqual(len(db.exec(SqlBuilder.select.table(AppConnectionCredentialCustody)).all()), 1)
        with self.assertRaises(AppGovernanceDenied):
            read_connection_service_template(self.other, self.connection.id)
        with self.assertRaises(AppGovernanceDenied):
            bind_app_service_template(self.owner, self.connection.app_key, self.setting.id, expected_revision=1)
        with self.assertRaises(AppGovernanceDenied):
            disable_app_service_template(self.owner, self.connection.app_key, expected_revision=1)

    async def test_template_change_requires_new_pin_and_never_overwrites_manual_binding(self):
        first = prepare_connection_service_destination(self.owner, self.connection.id, expected_template_revision=1)
        with DbSession.atomic() as db:
            self.setting.url = "https://approved.example/reviewed-new-target"
            db.update(self.setting)
        bind_app_service_template(self.other, self.connection.app_key, self.setting.id, expected_revision=1)
        with DbSession.atomic() as db, self.assertRaises(AppGovernanceConflict):
            current_destination(db, self.connection, custody.HANDOFF_EVENT)
        with self.assertRaises(AppGovernanceConflict):
            prepare_connection_service_destination(self.owner, self.connection.id,
                expected_template_revision=1, expected_destination_revision=first["revision"])
        refreshed = prepare_connection_service_destination(self.owner, self.connection.id,
            expected_template_revision=2, expected_destination_revision=first["revision"])
        self.assertEqual(refreshed["revision"], 2)
        bind_app_event_destination(self.other, self.connection.id, self.setting.id, 2)
        with self.assertRaises(AppGovernanceDenied):
            prepare_connection_service_destination(self.owner, self.connection.id,
                expected_template_revision=2, expected_destination_revision=3)

    async def test_disabling_shared_service_fences_a_previously_delivered_personal_credential(self):
        destination = prepare_connection_service_destination(self.owner, self.connection.id, expected_template_revision=1)
        custody.approve_credential_custody(self.owner, self.connection.id,
            expected_connection_revision=self.connection_revision(), expected_destination_revision=destination["revision"],
            expected_revision=1)
        delivered = await custody.handoff_connection_credential(self.owner, self.identifier,
            expected_revision=2, request_id="inherited")
        self.assertEqual(delivered["state"], "delivered")
        token = self.received[0]["credential"]["token"]
        self.assertTrue(custody.read_custodied_credential_status(token)["usable"])
        disable_app_service_template(self.other, self.connection.app_key, expected_revision=1)
        self.assertFalse(custody.read_custodied_credential_status(token)["usable"])
        with self.assertRaises(AppGovernanceDenied):
            authenticate_connection_credential(token)

    async def test_native_http_owner_prepares_only_the_administrator_approved_target(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
        from langboard.routes.settings import AppEventDestinationTemplateApi  # noqa: F401
        from langboard_shared.core.routing import AppRouter
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.Env import Env
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        with TestClient(app) as client:
            access, refresh = AuthSecurity.authenticate(self.owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": "Bearer " + access}
            denied_read = client.get(f"/settings/apps/registry/{self.connection.app_key}/service-template", headers=headers)
            self.assertEqual(denied_read.status_code, 403)
            admin_access, admin_refresh = AuthSecurity.authenticate(self.other.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, admin_refresh)
            admin_read = client.get(f"/settings/apps/registry/{self.connection.app_key}/service-template",
                headers={"Authorization": "Bearer " + admin_access})
            self.assertEqual(admin_read.status_code, 200, admin_read.text)
            self.assertEqual(admin_read.json()["template"]["revision"], 1)
            self.assertEqual(admin_read.headers["cache-control"], "no-store")
            self.assertNotIn("test-signing-ref", admin_read.text)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            base = f"/settings/apps/connections/{self.connection.get_uid()}"
            metadata = client.get(base + "/service-template", headers=headers)
            self.assertEqual(metadata.status_code, 200, metadata.text)
            self.assertEqual(metadata.json()["connection_revision"], self.connection_revision())
            self.assertEqual(metadata.json()["template"]["origin"], "https://approved.example")
            forbidden = client.post(f"/settings/apps/registry/{self.connection.app_key}/service-template",
                headers=headers, json={"webhook_uid": self.setting.get_uid(), "expected_revision": 1})
            self.assertEqual(forbidden.status_code, 403)
            injected = client.post(base + "/service-destination", headers=headers,
                json={"expected_template_revision": 1, "url": "https://unapproved.example"})
            self.assertIn(injected.status_code, (400, 422))
            prepared = client.post(base + "/service-destination", headers=headers,
                json={"expected_template_revision": 1})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertEqual(prepared.headers["cache-control"], "no-store")
            self.assertNotIn("test-signing-ref", prepared.text)

    async def test_generated_template_migration_preserves_manual_targets_and_retains_pins(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect, text
        from sqlalchemy.exc import IntegrityError
        source = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261116215106-ca931f192ad6.py"
        spec = importlib.util.spec_from_file_location("service_template_schema", source)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE app_event_destination (id BIGINT PRIMARY KEY, "
                "connection_id BIGINT NOT NULL UNIQUE, webhook_id BIGINT NOT NULL, revision INTEGER NOT NULL, "
                "target_revision TEXT NOT NULL, trust_revision TEXT NOT NULL, is_enabled BOOLEAN NOT NULL)"))
            connection.execute(text("INSERT INTO app_event_destination VALUES (1,2,3,1,'target','trust',1)"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            self.assertIn("template_id", {row["name"] for row in inspect(connection).get_columns("app_event_destination")})
            self.assertEqual(connection.execute(text("SELECT webhook_id FROM app_event_destination WHERE id=1")).scalar_one(), 3)
            with self.assertRaises(IntegrityError):
                connection.execute(text("UPDATE app_event_destination SET template_id=5"))
            connection.execute(text("UPDATE app_event_destination SET template_id=5,template_revision=1"))
            with self.assertRaisesRegex(RuntimeError, "pins"):
                migration.downgrade()
            connection.execute(text("UPDATE app_event_destination SET template_id=NULL,template_revision=NULL"))
            migration.downgrade()
            self.assertNotIn("template_id", {row["name"] for row in inspect(connection).get_columns("app_event_destination")})
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app_event_destination")).scalar_one(), 1)
            migration.upgrade()
            connection.execute(text("INSERT INTO app_event_destination_template "
                "(id,app_key,webhook_id,target_revision,trust_revision,revision,is_enabled) "
                "VALUES (5,'test',3,'target','trust',1,1)"))
            with self.assertRaisesRegex(RuntimeError, "history"):
                migration.downgrade()
