"""Elapsed budget rejects drip responses despite individual reads succeeding."""

import json
import httpx
import pytest
from langboard.apps import MetadataTransport as transport


@pytest.mark.parametrize("body", [None, {}, {"offset": 0, "size": 25}])
@pytest.mark.parametrize("status", [200, 302, 401])
def test_read_only_discovery_preserves_request_and_status_boundary(monkeypatch, body, status):
    requests = []

    def external(request):
        requests.append(request)
        return httpx.Response(status, json={"items": []}, headers={"Location": "https://other.invalid/"})

    client = httpx.Client
    monkeypatch.setattr(
        transport.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(external), **kwargs)
    )
    args = ("https://deploy.example.invalid", "/orchestrator/app/list", {"token": "test-only"})
    if status == 200:
        assert transport.read_json(*args, params={"page": 1}, json_body=body)[0] == {"items": []}
    else:
        with pytest.raises(transport.MetadataUnavailable):
            transport.read_json(*args, params={"page": 1}, json_body=body)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == ("GET" if body is None else "POST")
    assert request.url.path == "/orchestrator/app/list"
    assert request.url.params["page"] == "1"
    assert request.headers["token"] == "test-only"
    if body is None:
        assert request.content == b""
    else:
        assert json.loads(request.content) == body


@pytest.mark.parametrize("body", [None, {"offset": 0}])
@pytest.mark.parametrize("slow", [False, True])
def test_stream_elapsed_budget_closes_response(monkeypatch, slow, body):
    clock = [0.0]
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in (b'{"safe":', b"true}"):
                clock[0] += 8 if slow else 1
                yield chunk

        def close(self):
            closed.append(True)

    client = httpx.Client
    monkeypatch.setattr(transport, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        transport.httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())), **kwargs
        ),
    )
    if slow:
        with pytest.raises(transport.MetadataUnavailable):
            transport.read_json("https://deploy.example.invalid", "/api/project.all", {}, json_body=body)
    else:
        result, _ = transport.read_json("https://deploy.example.invalid", "/api/project.all", {}, json_body=body)
        assert result == {"safe": True}
    assert closed == [True]


def test_deep_json_within_byte_limit_closes_response(monkeypatch):
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"[" * 50000 + b"0" + b"]" * 50000

        def close(self):
            closed.append(True)

    client = httpx.Client
    monkeypatch.setattr(
        transport.httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())), **kwargs
        ),
    )
    with pytest.raises(transport.MetadataUnavailable):
        transport.read_json("https://deploy.example.invalid", "/api/notification.one", {})
    assert closed == [True]


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
@pytest.mark.parametrize("during_stream", [False, True])
def test_network_failure_preserves_http_error_and_closes_transport(monkeypatch, error_type, during_stream):
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"safe":'
            raise error_type("provider detail must not escape")

        def close(self):
            closed.append("stream")

    class Provider(httpx.MockTransport):
        def close(self):
            closed.append("client")

    def external(request):
        if not during_stream:
            raise error_type("provider detail must not escape", request=request)
        return httpx.Response(200, stream=Stream())

    client = httpx.Client
    monkeypatch.setattr(
        transport.httpx, "Client", lambda **kwargs: client(transport=Provider(external), **kwargs)
    )
    with pytest.raises(error_type):
        transport.read_json("https://errors.example.invalid", "/api/0/organizations/", {})
    assert closed == (["stream", "client"] if during_stream else ["client"])
