"""Formal app event allowlists reach the existing service binding without broad fanout."""
import json
import unittest
from test_service_template_native import ServiceTemplateNativeTest
from langboard_shared.core.db import DbSession
from langboard_shared.domain.constants.AppEventType import APP_EVENTS, APP_CREDENTIAL_HANDOFF_EVENT, APP_NOTIFICATION_EVENT
from langboard_shared.domain.services.AppEventDestinationTemplates import bind_app_service_template
from langboard_shared.tasks.webhooks.WebhookTask import _accepts_event
from langboard_shared.tasks.webhooks.utils.WebhookModel import validate_webhook_events, WEBHOOK_EVENT_NAMES
from types import SimpleNamespace

class AppDirectedWebhookNativeTest(unittest.TestCase):
    setUp = ServiceTemplateNativeTest.setUp
    connection_revision = ServiceTemplateNativeTest.connection_revision

    def test_form_selection_can_bind_only_required_app_events(self):
        from langboard.routes.settings.Form import CreateWebhookForm
        events = [APP_CREDENTIAL_HANDOFF_EVENT, APP_NOTIFICATION_EVENT]
        form = CreateWebhookForm(name="Dedicated callback", url="https://approved.example/events", events=events)
        self.assertEqual(form.events, events)
        with DbSession.atomic() as db:
            self.setting.events = form.events
            db.update(self.setting)
        result = bind_app_service_template(self.other, self.connection.app_key, self.setting.id, expected_revision=1)
        self.assertEqual(result["revision"], 2)
        self.assertTrue(result["enabled"])
        for name in WEBHOOK_EVENT_NAMES:
            self.assertFalse(_accepts_event(self.setting, name))

    def test_app_catalog_is_explicit_and_general_all_events_cannot_fanout_apps(self):
        from langboard.routes.schemas.WebhookSchemaApi import webhook_openapi
        payload = json.loads(webhook_openapi().body)
        self.assertEqual(set(payload["x-langboard-app-events"]), APP_EVENTS)
        self.assertFalse(APP_EVENTS & set(payload["components"]["schemas"]))
        self.assertEqual(validate_webhook_events(sorted(APP_EVENTS)), sorted(APP_EVENTS))
        for name in APP_EVENTS:
            self.assertFalse(_accepts_event(SimpleNamespace(events=None), name))
            self.assertFalse(_accepts_event(SimpleNamespace(events=[]), name))
            self.assertTrue(_accepts_event(SimpleNamespace(events=[name]), name))
        for events in ([], [APP_NOTIFICATION_EVENT, APP_NOTIFICATION_EVENT], ["unknown.app.event"]):
            with self.assertRaises(ValueError):validate_webhook_events(events)
