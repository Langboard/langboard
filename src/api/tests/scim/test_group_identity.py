"""SCIM names are labels, never stable identifiers for membership replacement."""

import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ScimGroup, ScimGroupMember, User, UserIdentityLink
from langboard_shared.domain.services import DomainService
from sqlalchemy import create_engine, select


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
