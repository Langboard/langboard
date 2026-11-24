"""Installer-owned event endpoints using the existing service-template relation."""

from uuid import uuid4
from langboard_sdk.definition import validate_app_definition
from langboard_sdk.governance import declaration_trust_fingerprint
from sqlalchemy import select
from ...core.db import DbSession, SqlBuilder
from ...core.security import KeyVault
from ...publishers import AppSettingPublisher
from ...tasks.webhooks.utils import validate_webhook_events, validate_webhook_url
from ..models import AppDefinition, WebhookSetting
from ..models.AppEventDestinationTemplate import AppEventDestinationTemplate
from .AppEventDestination import APP_EVENTS, _target_revision
from .AppGovernance import AppGovernanceDenied


def webhook_owners(db, webhook_ids):
    """One primary query for the existing ownership relation, including disabled apps."""
    ids = list(webhook_ids)
    if not ids:
        return {}
    rows = db.exec(
        select(AppEventDestinationTemplate, AppDefinition)
        .join(AppDefinition, AppDefinition.key == AppEventDestinationTemplate.app_key)
        .where(AppEventDestinationTemplate.webhook_id.in_(ids))
    ).all()
    result = {}
    for template, definition in rows:
        result.setdefault(template.webhook_id, []).append(
            {
                "key": definition.key,
                "name": definition.declaration["name"],
                "is_enabled": definition.is_enabled,
            }
        )
    return result


def require_unmanaged_webhooks(db, webhook_ids):
    if webhook_owners(db, webhook_ids):
        raise AppGovernanceDenied("App-owned webhooks must be managed through their app installation")


def webhook_responses(settings):
    """Expose ownership without signing material or one query per endpoint."""
    with DbSession.use(readonly=False) as db:
        owners = webhook_owners(db, [setting.id for setting in settings])
        return [{**setting.api_response(), "owner_apps": owners.get(setting.id, [])} for setting in settings]


def reconcile_declared_webhook(db, definition, cleanup):
    """Run only inside the current administrator's approved installation transaction."""
    declaration = validate_app_definition(definition.declaration)
    spec = declaration.get("event_webhook")
    if spec is None:
        return None
    url = validate_webhook_url(spec["url"])
    events = validate_webhook_events(spec["events"])
    if not events or set(events) - APP_EVENTS:
        raise ValueError("App event webhook requires supported directed app events")
    template = db.exec(
        SqlBuilder.select.table(AppEventDestinationTemplate)
        .where(AppEventDestinationTemplate.app_key == definition.key)
        .with_for_update()
    ).first()
    setting = (
        None
        if template is None
        else db.exec(
            SqlBuilder.select.table(WebhookSetting).where(WebhookSetting.id == template.webhook_id).with_for_update()
        ).first()
    )
    revealed = None
    created = setting is None
    if setting is None:
        secret_id = str(uuid4())
        revealed = KeyVault.create_key(secret_id)
        cleanup.callback(KeyVault.delete_key, secret_id)
        setting = WebhookSetting(name=declaration["name"], url=url, events=events, secret_id=secret_id)
        db.insert(setting)
    elif any(owner["key"] != definition.key for owner in webhook_owners(db, [setting.id]).get(setting.id, [])):
        raise AppGovernanceDenied("A shared legacy endpoint needs an explicit ownership repair")
    changed = created or (setting.name, setting.url, setting.events, setting.is_enabled) != (
        declaration["name"],
        url,
        events,
        True,
    )
    setting.name, setting.url, setting.events, setting.is_enabled = declaration["name"], url, events, True
    if not created and changed:
        db.update(setting)
    target = _target_revision(setting)
    trust = declaration_trust_fingerprint(declaration)
    if template is None:
        template = AppEventDestinationTemplate(
            app_key=definition.key, webhook_id=setting.id, target_revision=target, trust_revision=trust
        )
        db.insert(template)
    elif not template.is_enabled or (template.target_revision, template.trust_revision) != (target, trust):
        template.target_revision, template.trust_revision = target, trust
        template.is_enabled = True
        template.revision += 1
        db.update(template)
        changed = True
    owner = {"key": definition.key, "name": declaration["name"], "is_enabled": definition.is_enabled}
    if created:
        db.after_commit(lambda: AppSettingPublisher.webhook_setting_created(setting))
    if created or changed:
        db.after_commit(
            lambda: AppSettingPublisher.webhook_setting_updated(
                setting.get_uid(),
                {
                    "name": setting.name,
                    "url": setting.url,
                    "events": setting.events,
                    "is_enabled": setting.is_enabled,
                    "owner_apps": [owner],
                },
            )
        )
    result = {
        "uid": setting.get_uid(),
        "app_key": definition.key,
        "url": setting.url,
        "events": setting.events,
        "revision": template.revision,
        "changed": changed,
    }
    if revealed is not None:
        result["signing_secret"] = revealed
    return result


def withdraw_declared_webhook(db, app_key):
    """Removing a declaration withdraws delivery, preserving ownership and keys."""
    template = db.exec(
        SqlBuilder.select.table(AppEventDestinationTemplate)
        .where(AppEventDestinationTemplate.app_key == app_key)
        .with_for_update()
    ).first()
    if template is None:
        return
    setting = db.exec(
        SqlBuilder.select.table(WebhookSetting).where(WebhookSetting.id == template.webhook_id).with_for_update()
    ).first()
    if template.is_enabled:
        template.is_enabled = False
        template.revision += 1
        db.update(template)
    if setting is not None and setting.is_enabled:
        setting.is_enabled = False
        db.update(setting)
        db.after_commit(lambda: AppSettingPublisher.webhook_setting_updated(setting.get_uid(), {"is_enabled": False}))
