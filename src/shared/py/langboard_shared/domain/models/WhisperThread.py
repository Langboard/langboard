"""Human-only discussions; never projected as ordinary comments or activity."""

from sqlalchemy import JSON, TEXT, CheckConstraint, Index, UniqueConstraint
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card
from .CardRelationship import CardRelationship
from .Project import Project
from .User import User


class WhisperThread(BaseDbModel, table=True):
    __table_args__ = (
        CheckConstraint("state IN ('OPEN', 'RESOLVED')", name="ck_whisper_thread_state"),
        CheckConstraint(
            "(card_id IS NOT NULL AND relationship_id IS NULL) OR "
            "(card_id IS NULL AND relationship_id IS NOT NULL AND subanchor_type IS NULL)",
            name="ck_whisper_thread_anchor",
        ),
        CheckConstraint(
            "(subanchor_type IS NULL AND subanchor_uid IS NULL) OR "
            "(subanchor_type IS NOT NULL AND subanchor_type IN ('content_block', 'comment', 'checklist', 'attachment') AND subanchor_uid IS NOT NULL)",
            name="ck_whisper_thread_subanchor",
        ),
    )
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    card_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Card, nullable=True, index=True)
    relationship_id: SnowflakeID | None = SnowflakeIDField(foreign_key=CardRelationship, nullable=True, index=True)
    subanchor_type: str | None = Field(default=None, nullable=True)
    subanchor_uid: str | None = Field(default=None, nullable=True)
    creator_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    state: str = Field(default="OPEN", nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self) -> list[str]:
        return []


class WhisperMessage(BaseDbModel, table=True):
    __table_args__ = (Index("ix_whisper_message_thread_id_id", "thread_id", "id"),)
    thread_id: SnowflakeID = SnowflakeIDField(foreign_key=WhisperThread, nullable=False)
    author_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    content: str = Field(nullable=False, sa_type=TEXT, repr=False)
    mention_uids: list[str] = Field(default_factory=list, nullable=False, sa_type=JSON, repr=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self) -> list[str]:
        return []


class WhisperParticipant(BaseDbModel, table=True):
    __table_args__ = (UniqueConstraint("thread_id", "user_id", name="uq_whisper_participant"),)
    thread_id: SnowflakeID = SnowflakeIDField(foreign_key=WhisperThread, nullable=False, index=True)
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False)
    last_read_message_id: SnowflakeID | None = SnowflakeIDField(foreign_key=WhisperMessage, nullable=True)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self) -> list[str]:
        return []
