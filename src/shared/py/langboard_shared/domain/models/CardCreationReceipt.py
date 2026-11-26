from ...core.db import BaseDbModel, Field, SnowflakeIDField


class CardCreationReceipt(BaseDbModel, table=True):
    """Actor-owned creation key retained even when its original card is removed."""

    actor_type: str = Field(nullable=False, max_length=4)
    actor_id: int = SnowflakeIDField(nullable=False)
    request_uuid: str = Field(nullable=False, max_length=36)
    payload_digest: str = Field(nullable=False, max_length=64)
    card_id: int | None = SnowflakeIDField(nullable=True)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["actor_type", "actor_id", "request_uuid"]
