"""Bot status reads use only the current project's cache."""

import json
import pytest
from langboard_shared.core.caching import Cache
from langboard_graph.core.graph.GraphRunner import GraphRunner
from langboard_graph.routes.HealthApi import bot_status_map


@pytest.mark.parametrize("current", [None, {}, {"card": {"card-a": "running"}}])
def test_bot_status_does_not_read_retired_global_cache(monkeypatch, current):
    requested = []

    def get(key):
        requested.append(key)
        if key == GraphRunner._get_bot_status_cache_key("project-a"):
            return current
        raise AssertionError("Retired global or unrelated project cache accessed")

    monkeypatch.setattr(Cache, "get", get)
    response = bot_status_map("project-a")
    assert json.loads(response.body) == {"status_map": current or {}}
    assert requested == [GraphRunner._get_bot_status_cache_key("project-a")]
