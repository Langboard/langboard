from typing import Any, ClassVar
from bcrypt import checkpw, gensalt, hashpw
from pydantic import field_validator
from ...core.db import ApiField, Field, SoftDeleteModel
from ...core.db.ColumnTypes import DateTimeField, ModelColumnType, SecretStr, SecretStrType
from ...core.storage import FileModel
from ...core.types import SafeDateTime, SnowflakeID
from ...core.utils.String import generate_random_string


class User(SoftDeleteModel, table=True):
    USER_TYPE: ClassVar[str] = "user"
    UNKNOWN_USER_TYPE: ClassVar[str] = "unknown"
    GROUP_EMAIL_TYPE: ClassVar[str] = "group_email"
    BCRYPT_HASH_PREFIXES: ClassVar[tuple[str, ...]] = ("$2a$", "$2b$", "$2x$", "$2y$")
    firstname: str = Field(nullable=False, api_field=ApiField())
    lastname: str = Field(nullable=False, api_field=ApiField())
    email: str = Field(nullable=False, index=True, api_field=ApiField())
    username: str = Field(
        default_factory=lambda: f"user-{generate_random_string(8)}", unique=True, nullable=False, api_field=ApiField()
    )
    password: SecretStr = Field(nullable=False, sa_type=SecretStrType)
    is_admin: bool = Field(default=False)
    avatar: FileModel | None = Field(
        default=None, sa_type=ModelColumnType(FileModel), api_field=ApiField(field_base_model="path")
    )
    preferred_lang: str = Field(default="en-US", nullable=False)
    activated_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "type": f"Enum[{User.USER_TYPE}, {User.UNKNOWN_USER_TYPE}, {User.GROUP_EMAIL_TYPE}]",
                **(schema or {}),
            }
        )

    def check_password(self, password: str) -> bool:
        try:
            return checkpw(password.encode(), self.password.get_secret_value().encode())
        except ValueError:
            return False

    def set_password(self, password: str) -> None:
        self.password = password

    def get_fullname(self) -> str:
        return f"{self.firstname} {self.lastname}"

    def api_response(self) -> dict[str, Any]:
        if self.deleted_at is not None:
            return User.create_unknown_user_api_response(self.get_uid())

        return {
            **super().api_response(),
            "type": User.USER_TYPE,
        }

    def notification_data(self) -> dict[str, Any]:
        return self.api_response()

    @staticmethod
    def create_unknown_user_api_response(uid: str) -> dict[str, Any]:
        return {
            "type": User.UNKNOWN_USER_TYPE,
            "uid": uid,
            "firstname": "",
            "lastname": "",
            "email": "",
            "username": "",
            "avatar": None,
        }

    @staticmethod
    def create_email_user_api_response(user_id: SnowflakeID, email: str) -> dict[str, Any]:
        return {
            "type": User.GROUP_EMAIL_TYPE,
            "uid": user_id.to_short_code(),
            "firstname": email,
            "lastname": "",
            "email": email,
            "username": "",
            "avatar": None,
        }

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["firstname", "lastname", "email", "username", "is_admin", "preferred_lang", "activated_at"]

    @field_validator("password", mode="before")
    @classmethod
    def __validate_password(cls, value: Any) -> SecretStr:
        return cls.__normalize_password(value)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "password":
            value = self.__normalize_password(value)
        super().__setattr__(name, value)

    @classmethod
    def __normalize_password(cls, password: Any) -> SecretStr:
        if isinstance(password, SecretStr):
            password_value = password.get_secret_value()
        elif isinstance(password, str):
            password_value = password
        else:
            raise TypeError("Password must be a string")

        if cls.__is_password_hash(password_value):
            return SecretStr(password_value)
        return cls.__create_password(password_value)

    @classmethod
    def __is_password_hash(cls, password: str) -> bool:
        return len(password) == 60 and password.startswith(cls.BCRYPT_HASH_PREFIXES)

    @staticmethod
    def __create_password(password: str) -> SecretStr:
        return SecretStr(hashpw(password.encode(), gensalt()).decode())
