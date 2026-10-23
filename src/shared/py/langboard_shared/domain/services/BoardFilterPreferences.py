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


def board_filter_preferences(actor, project_id, *, pinned_filters=None, expected_revision=None):
    if expected_revision is not None:
        pins = TypeAdapter(list[BoardFilterPin]).validate_python(pinned_filters)
        if len(pins) > 32 or len({(pin.key, pin.value) for pin in pins}) != len(pins):
            raise ValueError("Filter shortcuts must be bounded and unique")
        pinned_filters = [pin.model_dump() for pin in pins]
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
                        pinned_filters=pinned_filters, revision=expected_revision + 1, updated_at=SafeDateTime.now()
                    )
                )
                if changed != 1:
                    raise PreferenceConflict()
                return {"revision": expected_revision + 1, "pinned_filters": pinned_filters}
            row = UserBoardFilterPreference(user_id=actor.id, project_id=project_id, pinned_filters=pinned_filters)
            db.insert(row)
        return {"revision": row.revision if row else 0, "pinned_filters": row.pinned_filters if row else []}
