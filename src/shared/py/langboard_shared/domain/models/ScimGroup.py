from typing import Any
from sqlalchemy import CheckConstraint
from ...core.db import ApiField, BaseDbModel, Field


class ScimGroup(BaseDbModel, table=True):
    __table_args__ = (
        CheckConstraint("source IN ('legacy-unresolved', 'local', 'scim')", name="ck_scim_group_source"),
        CheckConstraint(
            "(source = 'scim' AND issuer <> '' AND substr(issuer, 1, 1) > ' ' AND substr(issuer, length(issuer), 1) > ' ' AND issuer NOT LIKE '%/') "
            "OR (source IN ('legacy-unresolved', 'local') AND issuer = '')",
            name="ck_scim_group_issuer",
        ),
        CheckConstraint("source <> 'local' OR external_id IS NULL", name="ck_scim_group_local_identity"),
    )
    revision: int = Field(default=1, nullable=False, sa_column_kwargs={"server_default": "1"})
    source: str = Field(default="legacy-unresolved", nullable=False, unique_groups=("authority_external_id",))
    issuer: str = Field(default="", nullable=False, unique_groups=("authority_external_id",))
    external_id: str | None = Field(
        default=None, nullable=True, index=True, unique_groups=("authority_external_id",), api_field=ApiField()
    )
    display_name: str = Field(nullable=False, index=True, api_field=ApiField())

    @staticmethod
    def normalize_issuer(issuer: str | None) -> str:
        return (issuer or "").strip().rstrip("/")

    @classmethod
    def authority_filters(cls, issuer: str | None):
        normalized = cls.normalize_issuer(issuer)
        return (cls.source == "scim", cls.issuer == normalized, cls.issuer != "")

    def belongs_to_scim(self, issuer: str | None) -> bool:
        normalized = self.normalize_issuer(issuer)
        return bool(normalized) and self.source == "scim" and self.issuer == normalized

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["external_id", "display_name"]
