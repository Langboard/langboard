from ...core.db import BaseDbModel, Field, SnowflakeIDField


class ProjectCreationReceipt(BaseDbModel, table=True):
    """Durable actor-bound request identity, retained after project deletion."""

    actor_id: int = SnowflakeIDField(nullable=False)
    request_uuid: str = Field(nullable=False, max_length=36)
    payload_digest: str = Field(nullable=False, max_length=64)
    project_id: int = SnowflakeIDField(nullable=False)
    template_id: int = SnowflakeIDField(nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["actor_id", "request_uuid", "project_id"]
