"""Bounded, revision-checked personal shortcuts; never a board permission grant."""

from typing import Literal
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import UserBoardFilterPreference
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import _actor, _authorized_board
from .UserAppPreferences import PreferenceConflict


class BoardFilterPin(BaseModel):
    model_config = {"extra": "forbid"}
    key: Literal[
        "keyword",
        "members",
        "creators",
        "labels",
        "parents",
        "children",
        "columns",
        "workflow_stages",
        "unfinished",
        "deadlines",
        "priorities",
    ]
    value: str = Field(min_length=1, max_length=256, strict=True)

    @field_validator("value")
    @classmethod
    def nonempty_value(cls, value):
        if not value.strip():
            raise ValueError("Filter condition must not be blank")
        # Keyword whitespace is part of the existing URL filter's exact value.
        return value

    @model_validator(mode="after")
    def valid_deadline_preset(self):
        if self.key == "priorities":
            from ..contracts.CardPriority import validate_card_priority

            validate_card_priority(self.value)
        if self.key == "deadlines" and self.value not in {
            "today",
            "week",
            "month",
            "quarter",
            "half-year",
            "year",
            "overdue",
            "none",
        }:
            raise ValueError("Unknown deadline filter preset")
        return self


class BoardFilterView(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=64, strict=True)
    filters: list[BoardFilterPin] = Field(min_length=1, max_length=32)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Saved view name must not be blank")
        return value.strip()

    @field_validator("filters")
    @classmethod
    def unique_conditions(cls, conditions):
        if len({(item.key, item.value) for item in conditions}) != len(conditions):
            raise ValueError("Saved view conditions must be unique")
        return conditions


def validate_saved_views(value):
    views = TypeAdapter(list[BoardFilterView]).validate_python(value)
    if len(views) > 16 or len({view.name.casefold() for view in views}) != len(views):
        raise ValueError("Saved views must be bounded and have unique names")
    return [view.model_dump() for view in views]


def board_filter_preferences(actor, project_id, *, pinned_filters=None, saved_views=None, expected_revision=None):
    if expected_revision is not None:
        pins = TypeAdapter(list[BoardFilterPin]).validate_python(pinned_filters)
        if len(pins) > 32 or len({(pin.key, pin.value) for pin in pins}) != len(pins):
            raise ValueError("Filter shortcuts must be bounded and unique")
        pinned_filters = [pin.model_dump() for pin in pins]
        if saved_views is not None:
            saved_views = validate_saved_views(saved_views)
    with DbSession.atomic() as db:
        # The existing account lock also serializes the first preference insert.
        actor = _actor(db, actor, for_update=expected_revision is not None)
        _authorized_board(
            db, actor, project_id, action=ProjectRoleAction.Read, for_update=expected_revision is not None
        )
        row = db.exec(
            SqlBuilder.select.table(UserBoardFilterPreference).where(
                UserBoardFilterPreference.user_id == actor.id,
                UserBoardFilterPreference.project_id == project_id,
            )
        ).first()
        revision = row.revision if row else 0
        views = saved_views if saved_views is not None else (row.saved_views if row else [])
        if expected_revision is not None:
            if revision != expected_revision:
                raise PreferenceConflict()
            if row:
                changed = db.exec(
                    update(UserBoardFilterPreference)
                    .where(
                        UserBoardFilterPreference.id == row.id,
                        UserBoardFilterPreference.revision == expected_revision,
                    )
                    .values(
                        pinned_filters=pinned_filters,
                        saved_views=views,
                        revision=expected_revision + 1,
                        updated_at=SafeDateTime.now(),
                    )
                )
                if changed != 1:
                    raise PreferenceConflict()
                return {"revision": expected_revision + 1, "pinned_filters": pinned_filters, "saved_views": views}
            row = UserBoardFilterPreference(
                user_id=actor.id, project_id=project_id, pinned_filters=pinned_filters, saved_views=views
            )
            db.insert(row)
        return {
            "revision": row.revision if row else 0,
            "pinned_filters": row.pinned_filters if row else [],
            "saved_views": row.saved_views if row else [],
        }
