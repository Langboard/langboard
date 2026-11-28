"""Reuse an administrator's app service binding without granting personal custody."""

from types import SimpleNamespace
from urllib.parse import urlsplit
from langboard_sdk.governance import declaration_trust_fingerprint
from ...core.db import DbSession, SqlBuilder
from ..models import AppDefinition, AppEventDestination, WebhookSetting
from ..models.AppEventDestinationTemplate import AppEventDestinationTemplate
from .AppConnectionAuthentication import _connection, _identity_hash, _manage
from .AppEventDestination import _target_revision, _validated_destination
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation


def _template(db, app_key):
    return db.exec(SqlBuilder.select.table(AppEventDestinationTemplate).where(
        AppEventDestinationTemplate.app_key == app_key).with_for_update()).first()


def _revision(value):
    if type(value) is not int or value < 1:
        raise ValueError("Invalid service template revision")
    return value


def _template_data(row, setting):
    # Show the service origin for informed consent, never its query, secret
    # reference, signing material or an arbitrary user-provided callback URL.
    url = urlsplit(setting.url)
    origin = f"https://{url.hostname}" + (f":{url.port}" if url.port and url.port != 443 else "")
    return {"template_uid": row.get_uid(), "revision": row.revision, "enabled": row.is_enabled, "origin": origin}



def read_app_service_template(actor, app_key):
    """Read a template's CAS fence without exposing signing configuration."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        if not _actor(db, actor).is_admin:
            raise AppGovernanceDenied()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key)).first()
        if definition is None:
            raise AppGovernanceDenied()
        row = _template(db, app_key)
        if row is None:
            return {"template": None}
        setting = _current(db, WebhookSetting, row.webhook_id)
        # Stale/disabled targets must remain repairable with the current fence.
        # Only the existing enabled destination validator establishes usability.
        current = False
        try:
            _validated_destination(db, app_key, row)
            current = True
        except (AppGovernanceDenied, AppGovernanceConflict):
            pass
        data = {"template_uid": row.get_uid(), "revision": row.revision, "enabled": row.is_enabled}
        if setting is not None:
            data["origin"] = _template_data(row, setting)["origin"]
        return {"template": data, "current": current}


def bind_app_service_template(actor, app_key, webhook_id, *, expected_revision=None):
    if expected_revision is not None:
        _revision(expected_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        if not user.is_admin:
            raise AppGovernanceDenied()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key)).first()
        setting = _current(db, WebhookSetting, webhook_id)
        if definition is None or setting is None:
            raise AppGovernanceDenied()
        if definition.declaration.get("event_webhook") is not None:
            raise AppGovernanceDenied("Declared app endpoints are managed by approved installation")
        target_revision = _target_revision(setting)
        trust_revision = declaration_trust_fingerprint(definition.declaration)
        candidate = SimpleNamespace(is_enabled=True, webhook_id=webhook_id,
            target_revision=target_revision, trust_revision=trust_revision)
        _validated_destination(db, app_key, candidate)
        row = _template(db, app_key)
        if (row is None and expected_revision is not None) or (row is not None and row.revision != expected_revision):
            raise AppGovernanceConflict()
        if row is None:
            row = AppEventDestinationTemplate(app_key=app_key, webhook_id=webhook_id,
                target_revision=target_revision, trust_revision=trust_revision)
            db.insert(row)
        elif not row.is_enabled or (row.webhook_id, row.target_revision, row.trust_revision) != (
                webhook_id, target_revision, trust_revision):
            row.webhook_id = webhook_id
            row.target_revision = target_revision
            row.trust_revision = trust_revision
            row.is_enabled = True
            row.revision += 1
            db.update(row)
        return _template_data(row, setting)


def read_connection_service_template(actor, connection_id):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        connection = _connection(db, connection_id)
        _manage(db, actor, connection)
        row = _template(db, connection.app_key)
        _, setting = _validated_destination(db, connection.app_key, row)
        destination = db.exec(SqlBuilder.select.table(AppEventDestination).where(
            AppEventDestination.connection_id == connection.id)).first()
        return {"connection_uid": connection.get_uid(), "connection_revision": _identity_hash(db, connection),
                "template": _template_data(row, setting),
                "destination_revision": destination.revision if destination else None,
                "can_prepare": destination is None or destination.template_id is not None}


def prepare_connection_service_destination(actor, connection_id, *, expected_template_revision,
                                         expected_destination_revision=None):
    """Pin approved configuration only; do not issue credentials or grant custody."""
    _revision(expected_template_revision)
    if expected_destination_revision is not None:
        _revision(expected_destination_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        connection = _connection(db, connection_id)
        _manage(db, actor, connection)
        template = _template(db, connection.app_key)
        _validated_destination(db, connection.app_key, template)
        if template.revision != expected_template_revision:
            raise AppGovernanceConflict()
        row = db.exec(SqlBuilder.select.table(AppEventDestination).where(
            AppEventDestination.connection_id == connection.id).with_for_update()).first()
        if row is not None and row.template_id is None:
            # A personal flow must never replace an explicit administrator bind.
            raise AppGovernanceDenied()
        if (row is None and expected_destination_revision is not None) or (
                row is not None and row.revision != expected_destination_revision):
            raise AppGovernanceConflict()
        if row is None:
            row = AppEventDestination(connection_id=connection.id, webhook_id=template.webhook_id,
                target_revision=template.target_revision, trust_revision=template.trust_revision,
                template_id=template.id, template_revision=template.revision)
            db.insert(row)
        elif not row.is_enabled or row.template_revision != template.revision or row.template_id != template.id:
            row.webhook_id = template.webhook_id
            row.target_revision = template.target_revision
            row.trust_revision = template.trust_revision
            row.template_id = template.id
            row.template_revision = template.revision
            row.is_enabled = True
            row.revision += 1
            db.update(row)
        return {"destination_uid": row.get_uid(), "revision": row.revision,
                "template_uid": template.get_uid(), "template_revision": template.revision}


def validate_inherited_destination(db, connection, destination, event_type=None):
    template = _current(db, AppEventDestinationTemplate, destination.template_id)
    if template is None or template.app_key != connection.app_key:
        raise AppGovernanceDenied()
    _validated_destination(db, connection.app_key, template, event_type)
    if (template.revision != destination.template_revision
            or (template.webhook_id, template.target_revision, template.trust_revision) != (
                destination.webhook_id, destination.target_revision, destination.trust_revision)):
        raise AppGovernanceConflict()


def disable_app_service_template(actor, app_key, *, expected_revision):
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        if not _actor(db, actor).is_admin:
            raise AppGovernanceDenied()
        row = _template(db, app_key)
        if row is None:
            raise AppGovernanceDenied()
        if row.revision != _revision(expected_revision):
            raise AppGovernanceConflict()
        if row.is_enabled:
            row.is_enabled = False
            row.revision += 1
            db.update(row)
        return {"template_uid": row.get_uid(), "revision": row.revision, "enabled": False}
