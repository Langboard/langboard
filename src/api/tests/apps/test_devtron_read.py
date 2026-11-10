"""Provider requests have closed endpoints and unambiguous resource selectors."""

import json
import httpx
import pytest
from langboard.apps import DevtronRead as devtron
from langboard.apps import MetadataTransport as transport
from langboard_shared.Env import Env


@pytest.mark.parametrize("operation,arguments", [
    ("https://attacker.invalid", {}),
    ("applications.list", {"url": "https://attacker.invalid"}),
    ("applications.list", {"size": True}),
    ("applications.list", {"offset": -1}),
    ("applications.list", {"search": "x" * 201}),
    ("deployments.list", {}),
    ("deployments.list", {"appId": 1}),
    ("deployments.list", {"appId": True, "environmentId": 2}),
    ("deployments.list", {"appId": 1, "environmentId": 2, "installedAppId": 3}),
    ("deployments.list", {"installedAppId": None}),
    ("deployments.list", {"installedAppId": 3, "method": "DELETE"}),
    ("deployments.list", {"helmAppId": "1|default|release", "installedAppId": 3}),
    ("deployments.list", {"helmAppId": "1|default|../release"}),
    ("deployments.list", {"helmAppId": "01|default|release"}),
    ("deployments.list", {"helmAppId": "1|default|release", "limit": 251}),
])
def test_invalid_or_ambiguous_reads_stop_before_network(monkeypatch, operation, arguments):
    monkeypatch.setattr(devtron, "read_json", lambda *args, **kwargs: pytest.fail("Unexpected provider request"))
    with pytest.raises(ValueError):
        devtron.read_metadata("https://devtron.example.invalid", "fixture-token", operation, arguments)


@pytest.mark.parametrize("base,token", [
    ("https://other.example.invalid", "fixture-token"),
    ("https://devtron.example.invalid", "token\r\ninjected: header"),
    ("https://devtron.example.invalid", "short"),
])
def test_unapproved_destination_and_invalid_credentials_stop_before_network(monkeypatch, base, token):
    monkeypatch.setattr(Env, "get_from_env", lambda *args: "https://devtron.example.invalid")
    monkeypatch.setattr(devtron, "read_json", lambda *args, **kwargs: pytest.fail("Unexpected provider request"))
    with pytest.raises(ValueError):
        devtron.read_metadata(base, token, "applications.list", {})


@pytest.mark.parametrize("mode", ["applications", "native", "installed", "external"])
def test_reviewed_reads_use_exact_origin_method_filters_and_identity(monkeypatch, mode):
    monkeypatch.setattr(Env, "get_from_env", lambda *args: "https://devtron.example.invalid")
    calls = []

    def external(request):
        calls.append(request)
        assert request.url.host == "devtron.example.invalid"
        assert request.headers["token"] == "fixture-token"
        if mode == "applications":
            assert request.method == "POST" and request.url.path == "/orchestrator/app/list"
            assert json.loads(request.content) == {"appNameSearch": "api", "offset": 0, "size": 25}
            result = {"appContainers": [], "appCount": 0}
        else:
            assert request.method == "GET" and request.content == b""
            if mode == "native":
                assert request.url.path == "/orchestrator/resource/history/deployment/cd-pipeline/v1"
                assert request.url.params.get_list("filterCriteria") == [
                    "application/devtron-application|id|17", "environment|id|4"
                ]
                assert request.url.params["limit"] == "50"
                result = {"cdWorkflows": []}
            else:
                assert request.url.path == "/orchestrator/application/deployment-history"
                if mode == "installed":
                    assert request.url.params["installedAppId"] == "3"
                    result = {"installedAppInfo": {"installedAppId": 3}, "deploymentHistory": []}
                else:
                    assert request.url.params["appId"] == "1|default|release"
                    # Unlinked external Helm releases do not echo installedAppInfo.
                    result = {"deploymentHistory": []}
        return httpx.Response(200, json={"code": 200, "result": result})

    client = httpx.Client
    monkeypatch.setattr(transport.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(external), **kwargs))
    operation, arguments = {
        "applications": ("applications.list", {"search": "api", "size": 25}),
        "native": ("deployments.list", {"appId": 17, "environmentId": 4}),
        "installed": ("deployments.list", {"installedAppId": 3}),
        "external": ("deployments.list", {"helmAppId": "1|default|release"}),
    }[mode]
    assert isinstance(devtron.read_metadata("https://devtron.example.invalid", "fixture-token", operation, arguments), dict)
    assert len(calls) == 1


@pytest.mark.parametrize("result,requested_id", [
    ({"installedAppInfo": {"installedAppId": True}, "deploymentHistory": []}, 1),
    ({"installedAppInfo": {"installedAppId": 3.0}, "deploymentHistory": []}, 3),
    ({"installedAppInfo": {"installedAppId": 4}, "deploymentHistory": []}, 3),
    ({"installedAppInfo": {"installedAppId": 3}, "deploymentHistory": [{}] * 251}, 3),
    ({"installedAppInfo": {"installedAppId": 3}, "deploymentHistory": ["not a record"]}, 3),
])
def test_history_identity_and_response_bounds_are_not_inferred(monkeypatch, result, requested_id):
    monkeypatch.setattr(Env, "get_from_env", lambda *args: "https://devtron.example.invalid")
    monkeypatch.setattr(devtron, "read_json", lambda *args, **kwargs: ({"code": 200, "result": result}, {}))
    with pytest.raises(transport.MetadataUnavailable):
        devtron.read_metadata(
            "https://devtron.example.invalid", "fixture-token", "deployments.list", {"installedAppId": requested_id}
        )
