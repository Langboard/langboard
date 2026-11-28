"""SCIM names are labels, never stable identifiers for membership replacement."""

import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.exceptions import ScimProvisioningException
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ScimGroup, ScimGroupMember, User, UserIdentityLink
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import create_engine, select


@pytest.fixture(autouse=True)
def explicit_authority(monkeypatch):
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: "https://directory.example"))


@pytest.mark.parametrize("existing_external_id", [None, "directory-original"])
@pytest.mark.parametrize("incoming_external_id", [None, "directory-new"])
def test_same_name_groups_do_not_replace_another_groups_identity_or_members(
    tmp_path, monkeypatch, existing_external_id, incoming_external_id
):
    engine = create_engine(f"sqlite:///{tmp_path / 'groups.sqlite'}")
    for model in (User, UserIdentityLink, ScimGroup, ScimGroupMember):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        with DbSession.atomic() as db:
            first = User(
                firstname="First",
                lastname="Member",
                email="first@example.invalid",
                password="fixture",
                activated_at=SafeDateTime.now(),
            )
            second = User(
                firstname="Second",
                lastname="Member",
                email="second@example.invalid",
                password="fixture",
                activated_at=SafeDateTime.now(),
            )
            db.insert(first)
            db.insert(second)
        service = DomainService().scim_provisioning
        original = service.create_group(
            {
                "externalId": existing_external_id,
                "displayName": "Engineering",
                "members": [{"value": first.get_uid()}],
            }
        )
        incoming = {"displayName": "Engineering", "members": [{"value": second.get_uid()}]}
        if incoming_external_id is not None:
            incoming["externalId"] = incoming_external_id
        created = service.create_or_upsert_group(incoming)
        assert created.id != original.id
        with engine.connect() as db:
            groups = db.execute(select(ScimGroup.id, ScimGroup.external_id)).all()
            members = db.execute(select(ScimGroupMember.group_id, ScimGroupMember.user_id)).all()
        assert dict(groups) == {original.id: existing_external_id, created.id: incoming_external_id}
        assert set(members) == {(original.id, first.id), (created.id, second.id)}
        if incoming_external_id is not None:
            # The same external ID remains idempotent and may rename/update its own group.
            retried = service.create_or_upsert_group({**incoming, "displayName": "Renamed Engineering"})
            assert retried.id == created.id
            with engine.connect() as db:
                assert len(db.execute(select(ScimGroup.id)).all()) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ["retry", "create", "rename"])
def test_group_write_identity_checks_use_primary_when_replica_is_behind(tmp_path, monkeypatch, operation):
    primary = create_engine(f"sqlite:///{tmp_path / 'primary.sqlite'}")
    replica = create_engine(f"sqlite:///{tmp_path / 'replica.sqlite'}")
    for engine in (primary, replica):
        for model in (User, UserIdentityLink, ScimGroup, ScimGroupMember):
            model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: primary)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
    try:
        service = DomainService().scim_provisioning
        payload = {"externalId": "stable-directory-id", "displayName": "Engineering"}
        original = service.create_group(payload)
        # The committed identity has not reached the replica yet.
        assert service.repo.scim_group.get_by_external_id(payload["externalId"]) is None
        if operation == "retry":
            retried = service.create_or_upsert_group({**payload, "displayName": "Renamed Engineering"})
            assert retried.id == original.id
        elif operation == "create":
            with pytest.raises(ScimProvisioningException.Conflict):
                service.create_group(payload)
        else:
            another = service.create_group({"externalId": "other-directory-id", "displayName": "Other"})
            with pytest.raises(ScimProvisioningException.Conflict):
                service.apply_group_mutations(another, {"externalId": payload["externalId"]})
        with primary.connect() as db:
            groups = dict(db.execute(select(ScimGroup.id, ScimGroup.external_id)).all())
        expected = {original.id: payload["externalId"]}
        if operation == "rename":
            expected[another.id] = "other-directory-id"
        assert groups == expected
    finally:
        primary.dispose()
        replica.dispose()
