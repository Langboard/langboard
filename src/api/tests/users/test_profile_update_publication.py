"""Committed profile edits must reach the existing account and settings subscribers."""

from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import User, UserProfile
from langboard_shared.domain.services import DomainService
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from langboard_shared.security import Auth
from sqlalchemy import create_engine, select


@pytest.mark.parametrize("has_profile", [True, False])
def test_profile_edits_publish_only_after_commit(tmp_path, monkeypatch, has_profile):
    engine = create_engine(f"sqlite:///{tmp_path / 'profile-publication.sqlite'}")
    for model in (User, UserProfile):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for publisher, name in ((UserPublisher, "updated"), (AppSettingPublisher, "user_updated"), (Auth, "reset_user")):
        monkeypatch.setattr(publisher, name, Mock())
    service = DomainService()
    with DbSession.atomic() as db:
        user = User(
            email="profile@example.invalid",
            firstname="Profile",
            lastname="Fixture",
            password="fixture-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        if has_profile:
            db.insert(UserProfile(user_id=user.id, industry="Old industry", purpose="Old purpose"))
    changes = {
        "industry": "New industry",
        "purpose": "New purpose",
        "affiliation": "New affiliation",
        "position": "New position",
    }
    try:
        with DbSession.atomic():
            assert service.user.update(user, changes, from_setting=True)
            UserPublisher.updated.assert_not_called()
            AppSettingPublisher.user_updated.assert_not_called()
            Auth.reset_user.assert_not_called()
        with engine.connect() as db:
            row = db.execute(select(UserProfile).where(UserProfile.user_id == user.id)).mappings().one()
            assert {key: row[key] for key in changes} == changes
        assert UserPublisher.updated.call_args.args[1] == {}, "Profile data must stay off the public user topic"
        AppSettingPublisher.user_updated.assert_called_once_with(user.get_uid(), changes)
        Auth.reset_user.assert_called_once()

        # A normal account edit still publishes only the changed public field.
        UserPublisher.updated.reset_mock()
        AppSettingPublisher.user_updated.reset_mock()
        assert service.user.update(user, {"firstname": "Renamed"}, from_setting=True)
        assert UserPublisher.updated.call_args.args[1] == {"firstname": "Renamed"}
        AppSettingPublisher.user_updated.assert_called_once_with(user.get_uid(), {"firstname": "Renamed"})

        UserPublisher.updated.reset_mock()
        AppSettingPublisher.user_updated.reset_mock()
        Auth.reset_user.reset_mock()
        with pytest.raises(RuntimeError, match="Injected rollback"):
            with DbSession.atomic():
                assert service.user.update(user, {"affiliation": "Rolled back"}, from_setting=True)
                raise RuntimeError("Injected rollback")
        with engine.connect() as db:
            assert (
                db.execute(select(UserProfile.affiliation).where(UserProfile.user_id == user.id)).scalar_one()
                == changes["affiliation"]
            )
        UserPublisher.updated.assert_not_called()
        AppSettingPublisher.user_updated.assert_not_called()
        Auth.reset_user.assert_not_called()
    finally:
        service.close()
        engine.dispose()
