from enum import Enum


class ApiPermission(Enum):
    Read = "read"
    Create = "create"
    Edit = "edit"
    Delete = "delete"
