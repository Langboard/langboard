"""Official client serialization and validation with an in-process node boundary."""

import json
from math import ceil
import pytest
from langboard_shared.core.search import ElasticsearchKeywordSearch as keyword
from langboard_shared.domain.services import KeywordSearchSettings as settings


INDEX = "fixture-keywords"


def hit(board, number=0, **overrides):
    return {
        "_source": {
            "active": True,
            "board_uid": board,
            "card_uid": f"{number:011d}",
            "attachment_uid": f"{number + 100:011d}",
            "projection_hash": "a" * 64,
            "text": "untrusted remote text",
            **overrides,
        }
    }


def response(hits):
    return {"timed_out": False, "_shards": {"failed": 0}, "hits": {"hits": hits}}


@pytest.fixture
def official_client(monkeypatch):
    pytest.importorskip("elasticsearch")
    from elastic_transport import ApiResponseMeta, HttpHeaders, Urllib3HttpNode
    from elastic_transport._node._base import NodeApiResponse

    state = {"requests": [], "closed": []}
    monkeypatch.setattr(settings, "configuration", lambda **_: {"index": INDEX})
    monkeypatch.setattr(
        settings,
        "runtime_options",
        lambda *_args, **_kwargs: {"hosts": "https://fixture.invalid", "max_retries": 0},
    )

    def perform_request(node, method, target, body=None, headers=None, **_kwargs):
        lines = [json.loads(line) for line in body.splitlines()]
        state["requests"].append((node, method, target, lines, headers))
        if "respond" in state:
            value = state["respond"](lines)
        elif target.endswith("/_msearch"):
            value = {
                "responses": [
                    response([hit(line["query"]["bool"]["filter"][0]["term"]["board_uid"])]) for line in lines[1::2]
                ]
            }
        else:
            value = response([hit(lines[0]["query"]["bool"]["filter"][0]["term"]["board_uid"])])
        return NodeApiResponse(
            ApiResponseMeta(
                200,
                "1.1",
                HttpHeaders({"content-type": "application/json", "x-elastic-product": "Elasticsearch"}),
                0,
                node.config,
            ),
            json.dumps(value).encode(),
        )

    monkeypatch.setattr(Urllib3HttpNode, "perform_request", perform_request)
    monkeypatch.setattr(Urllib3HttpNode, "close", lambda node: state["closed"].append(node))
    return state


def test_many_boards_keep_individual_budget_and_one_client(official_client):
    boards = [f"{number:011d}" for number in range(keyword.DOCUMENT_CANDIDATE_BATCH_SIZE + 3)]

    def respond(lines):
        return {
            "responses": [
                response([hit(line["query"]["bool"]["filter"][0]["term"]["board_uid"], number) for number in range(25)])
                for line in lines[1::2]
            ]
        }

    official_client["respond"] = respond
    result = keyword.search_document_candidates_many([*boards, boards[0]], "billing")
    assert list(result) == boards
    assert all(len(result[board]) == 20 for board in boards)
    assert sum(map(len, result.values())) == len(boards) * 20
    requests = official_client["requests"]
    assert len(requests) == ceil(len(boards) / keyword.DOCUMENT_CANDIDATE_BATCH_SIZE)
    assert len({id(request[0]) for request in requests}) == 1
    assert official_client["closed"] == [requests[0][0]]
    searched = []
    for _, method, target, lines, headers in requests:
        assert method == "POST" and target == f"/{INDEX}/_msearch"
        assert "ndjson" in headers["content-type"]
        assert len(lines) <= keyword.DOCUMENT_CANDIDATE_BATCH_SIZE * 2
        for header, body in zip(lines[::2], lines[1::2]):
            assert header == {"allow_partial_search_results": False}
            assert body["size"] == 20 and body["timeout"] == "500ms" and body["track_total_hits"] is False
            assert body["_source"] == ["active", "board_uid", "card_uid", "attachment_uid", "projection_hash"]
            assert body["query"]["bool"]["must"] == [{"match": {"text": {"query": "billing", "operator": "and"}}}]
            assert body["query"]["bool"]["filter"][1] == {"term": {"active": True}}
            searched.append(body["query"]["bool"]["filter"][0]["term"]["board_uid"])
    assert searched == boards
    assert "untrusted remote text" not in json.dumps(result)
    assert all(
        set(candidate) == {"card_uid", "attachment_uid", "projection_hash"}
        for candidates in result.values()
        for candidate in candidates
    )


def test_malformed_and_foreign_sources_are_rejected(official_client):
    official_client["respond"] = lambda _: {
        "responses": [
            response(
                [
                    hit("board"),
                    None,
                    {"_source": []},
                    hit("foreign"),
                    hit("board", active=1),
                    hit("board", card_uid="bad"),
                    hit("board", attachment_uid=1),
                    hit("board", projection_hash="A" * 64),
                    hit("board", projection_hash=None),
                ]
            )
        ]
    }
    result = keyword.search_document_candidates_many(["board"], "billing")
    assert result == {
        "board": [{key: hit("board")["_source"][key] for key in ("card_uid", "attachment_uid", "projection_hash")}]
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"error": "private"},
        {"status": 503, **response([hit("second")])},
        {**response([hit("second")]), "timed_out": True},
        {**response([hit("second")]), "_shards": {"failed": 1}},
        {**response([]), "hits": {"hits": None}},
    ],
)
def test_bad_subresponse_preserves_other_boards(official_client, bad):
    official_client["respond"] = lambda _: {"responses": [response([hit("first")]), bad, response([hit("third")])]}
    result = keyword.search_document_candidates_many(["first", "second", "third"], "query")
    assert len(result["first"]) == len(result["third"]) == 1
    assert result["second"] == []


def test_missing_and_reordered_parts_fail_closed(official_client):
    official_client["respond"] = lambda _: {"responses": [response([hit("second")]), response([hit("first")])]}
    assert keyword.search_document_candidates_many(["first", "second", "third"], "query") == {
        "first": [],
        "second": [],
        "third": [],
    }
    official_client["respond"] = lambda _: {"responses": [response([hit("first")])]}
    result = keyword.search_document_candidates_many(["first", "second"], "query")
    assert len(result["first"]) == 1 and result["second"] == []


@pytest.mark.parametrize("body", [None, {}, {"responses": {}}, {"responses": [response([]), response([])]}])
def test_malformed_envelope_fails_closed(official_client, body):
    official_client["respond"] = lambda _: body
    assert keyword.search_document_candidates_many(["first"], "query") == {"first": []}


def test_failed_batch_retains_success_and_continues(official_client):
    boards = [str(number) for number in range(keyword.DOCUMENT_CANDIDATE_BATCH_SIZE * 2 + 1)]

    def respond(lines):
        if len(official_client["requests"]) == 2:
            raise RuntimeError("private provider failure")
        return {
            "responses": [
                response([hit(line["query"]["bool"]["filter"][0]["term"]["board_uid"])]) for line in lines[1::2]
            ]
        }

    official_client["respond"] = respond
    result = keyword.search_document_candidates_many(boards, "query")
    assert all(len(result[board]) == (0 if 50 <= number < 100 else 1) for number, board in enumerate(boards))
    assert len(official_client["requests"]) == 3 and len(official_client["closed"]) == 1


def test_no_configuration_and_invalid_queries_make_no_requests(official_client, monkeypatch):
    monkeypatch.setattr(settings, "configuration", lambda **_: None)
    assert keyword.search_document_candidates_many(["board"], "query") is None
    assert keyword.search_document_candidates("board", "query") is None
    for query in (" ", "q" * 1001):
        assert keyword.search_document_candidates_many(["board"], query) == {"board": []}
        assert keyword.search_document_candidates("board", query) == []
    assert keyword.search_document_candidates_many([], "query") == {}
    assert official_client["requests"] == []


def test_configuration_failure_is_optional(official_client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("private configuration failure")

    monkeypatch.setattr(settings, "configuration", fail)
    assert keyword.search_document_candidates_many(["board"], "query") is None
    monkeypatch.setattr(settings, "configuration", lambda **_: {"index": INDEX})
    monkeypatch.setattr(settings, "runtime_options", fail)
    assert keyword.search_document_candidates_many(["board"], "query") == {"board": []}
    assert official_client["requests"] == []


def test_legacy_single_board_api_keeps_search_protocol(official_client):
    candidates = keyword.search_document_candidates("board", "billing")
    assert len(candidates) == 1
    assert official_client["requests"][0][2].startswith(f"/{INDEX}/_search")
    assert candidates == keyword.search_document_candidates_many(["board"], "billing")["board"]
    assert len(official_client["closed"]) == 2
