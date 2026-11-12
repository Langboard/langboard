"""Closed Devtron read policy; callers retain connection/resource authority.

This module is internal provider transport, not a panel proxy or authorization
entrypoint. A host handler must resolve the current secret and binding before
calling it and fence asynchronous results against that authority afterwards.
"""

import re
from .MetadataTransport import MetadataUnavailable, approved_instance, read_json


def _integer(value, minimum=1, maximum=1_000_000_000):
    return type(value) is int and minimum <= value <= maximum


def _helm_identity(value):
    parts = value.split("|") if isinstance(value, str) else []
    label = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if (
        len(parts) != 3
        or not re.fullmatch(r"[1-9][0-9]{0,9}", parts[0])
        or not _integer(int(parts[0]))
        or not 1 <= len(parts[1]) <= 63
        or not re.fullmatch(label, parts[1])
        or not 1 <= len(parts[2]) <= 53
        or not all(re.fullmatch(label, part) for part in parts[2].split("."))
    ):
        raise ValueError("Invalid external Helm identity")


def read_spec(operation, arguments):
    """Build only reviewed read requests, never a caller-supplied URL/method."""
    if not isinstance(arguments, dict):
        raise ValueError("Invalid Devtron read arguments")
    if operation == "applications.list":
        if arguments.keys() - {"search", "offset", "size"}:
            raise ValueError("Unknown application filter")
        search, offset, size = arguments.get("search", ""), arguments.get("offset", 0), arguments.get("size", 50)
        if not isinstance(search, str) or len(search) > 200 or not _integer(offset, 0) or not _integer(size, 1, 250):
            raise ValueError("Invalid application filter")
        return "/orchestrator/app/list", None, {"appNameSearch": search, "offset": offset, "size": size}
    if operation != "deployments.list" or arguments.keys() - {
        "appId", "environmentId", "installedAppId", "helmAppId", "limit"
    }:
        raise ValueError("Unsupported Devtron read")
    native = "appId" in arguments or "environmentId" in arguments
    installed, external = "installedAppId" in arguments, "helmAppId" in arguments
    if sum((native, installed, external)) != 1:
        raise ValueError("Select one deployment scope")
    limit = arguments.get("limit", 50)
    if not _integer(limit, 1, 250):
        raise ValueError("Invalid deployment limit")
    if external:
        _helm_identity(arguments["helmAppId"])
        return "/orchestrator/application/deployment-history", {"appId": arguments["helmAppId"]}, None
    if installed:
        if not _integer(arguments["installedAppId"]):
            raise ValueError("Invalid installed application identity")
        return "/orchestrator/application/deployment-history", {"installedAppId": arguments["installedAppId"]}, None
    if not _integer(arguments.get("appId")) or not _integer(arguments.get("environmentId")):
        raise ValueError("Invalid application/environment identity")
    return "/orchestrator/resource/history/deployment/cd-pipeline/v1", [
        ("filterCriteria", f"application/devtron-application|id|{arguments['appId']}"),
        ("filterCriteria", f"environment|id|{arguments['environmentId']}"),
        ("offset", 0), ("limit", limit),
    ], None


def read_metadata(base, token, operation, arguments):
    """Return bounded internal data for the app adapter's interpretation.

    Never return this provider payload directly from a route or to a frame.
    """
    path, params, body = read_spec(operation, arguments)
    base = approved_instance(base)
    if not isinstance(token, str) or not 8 <= len(token) <= 4096 or any(not 33 <= ord(char) < 127 for char in token):
        raise ValueError("Invalid Devtron credential")
    payload, _ = read_json(base, path, {"token": token, "Accept": "application/json"}, params, json_body=body)
    if not isinstance(payload, dict) or payload.get("code") != 200 or not isinstance(payload.get("result"), dict):
        raise MetadataUnavailable()
    result = payload["result"]
    if operation == "applications.list":
        rows = result.get("appContainers")
    elif "appId" in arguments and "environmentId" in arguments:
        rows = result.get("cdWorkflows")
    else:
        rows = result.get("deploymentHistory")
        if "installedAppId" in arguments:
            identity = result.get("installedAppInfo")
            if (
                not isinstance(identity, dict)
                or not _integer(identity.get("installedAppId"))
                or identity["installedAppId"] != arguments["installedAppId"]
            ):
                raise MetadataUnavailable()
    if not isinstance(rows, list) or len(rows) > 250 or any(not isinstance(row, dict) for row in rows):
        raise MetadataUnavailable()
    return result
