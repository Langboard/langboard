"""Selected scope is server-bound; only reviewed provider metadata can leave."""

from hashlib import sha256
import pytest
from langboard.apps import DevtronRead as devtron
from langboard.apps.MetadataTransport import MetadataUnavailable


@pytest.mark.parametrize("identity,filters,expected", [
    ("app-17-env-4", {}, {"appId": 17, "environmentId": 4, "limit": 50}),
    ("helm-app-3", {"limit": 10}, {"installedAppId": 3, "limit": 10}),
    ("helm-release-" + sha256(b"1|default|release").hexdigest(), {"helmAppId": "1|default|release"},
     {"helmAppId": "1|default|release", "limit": 50}),
])
def test_selected_resource_binds_all_three_provider_scopes(identity, filters, expected):
    assert devtron.selected_deployment_arguments("application", identity, filters) == expected


@pytest.mark.parametrize("identity,filters", [
    ("app-17-env-4", {"url": "https://other.invalid"}),
    ("app-17-env-4", {"appId": 99}),
    ("app-17-env-4", {"helmAppId": "1|default|release"}),
    ("helm-app-3", {"limit": True}),
    ("app-01-env-4", {}),
    ("app-1000000001-env-4", {}),
    ("helm-release-" + "0" * 64, {"helmAppId": "1|default|release"}),
])
def test_client_cannot_override_the_selected_scope(identity, filters):
    with pytest.raises(ValueError):
        devtron.selected_deployment_arguments("application", identity, filters)


@pytest.mark.parametrize("helm", [False, True])
def test_provider_projection_drops_private_payloads_and_uses_bounded_budget(monkeypatch, helm):
    arguments = {"installedAppId": 3} if helm else {"appId": 17, "environmentId": 4}
    native = {"id": 1_000_000_001, "workflow_type": "DEPLOY", "status": "Succeeded",
              "started_on": "2026-11-13T00:00:00Z", "finished_on": None,
              "gitTriggers": {"source": {"Commit": "a" * 40, "token": "private-token"}},
              "secret": "private-token", "logs": "private-body"}
    history = {"version": 1_000_000_001, "status": "deployed", "deployedAt": {"seconds": "1794528000"},
               "values": "private-token"}

    def read(base, token, operation, received, *, timeout):
        assert timeout == 8 and operation == "deployments.list" and received == arguments
        return {"deploymentHistory" if helm else "cdWorkflows": [history if helm else native],
                "credentials": "private-token"}

    monkeypatch.setattr(devtron, "read_metadata", read)
    result = devtron.read_selected_deployments("https://devtron.example.invalid", "fixture-token", arguments)
    assert len(result["items"]) == 1
    assert "private" not in str(result) and "credentials" not in str(result)
    if not helm:
        assert result["items"][0]["gitTriggers"] == {"0": {"Commit": "a" * 40}}


@pytest.mark.parametrize("value", ["fixture-token", "not-a-date"])
def test_nonmetadata_timestamp_is_rejected(monkeypatch, value):
    monkeypatch.setattr(devtron, "read_metadata", lambda *a, **k: {"cdWorkflows": [
        {"id": 1, "workflow_type": "DEPLOY", "status": "running", "started_on": value}
    ]})
    with pytest.raises(MetadataUnavailable):
        devtron.read_selected_deployments("https://devtron.example.invalid", "fixture-token", {"appId": 1})
