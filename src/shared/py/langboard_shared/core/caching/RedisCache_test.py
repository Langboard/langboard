"""Actual Redis isolation checks; the URL must point to a disposable test server."""

import importlib
import os
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import pytest
from redis import Redis
from redis.exceptions import NoPermissionError


@pytest.fixture
def redis_scope(monkeypatch):
    url = os.environ.get("LANGBOARD_CACHE_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set LANGBOARD_CACHE_TEST_REDIS_URL to a disposable Redis server")
    module = importlib.import_module("langboard_shared.core.caching.RedisCache")
    client = Redis.from_url(url, decode_responses=True)
    root = f"test:{uuid4().hex}:"
    caches = []

    def cache(suffix):
        monkeypatch.setattr(module, "Env", SimpleNamespace(CACHE_URL=url, CACHE_KEY_PREFIX=root + suffix))
        instance = module.RedisCache()
        caches.append(instance)
        return instance

    yield client, root, cache
    for instance in caches:
        instance._cache.close()
    for key in client.scan_iter(match=root + "*"):
        client.delete(key)
    client.close()


def test_scoped_values_and_atomic_claims_do_not_collide(redis_scope):
    client, root, cache = redis_scope
    first, second = cache("first:"), cache("second:")
    first.set("same", {"owner": "first"}, ttl=60)
    second.set("same", {"owner": "second"}, ttl=60)
    assert first.get("same") == {"owner": "first"}
    assert second.get("same") == {"owner": "second"}
    assert 0 < client.ttl(root + "first:same") <= 60
    assert first.set_if_absent("claim", "first", ttl=60)
    assert not first.set_if_absent("claim", "again", ttl=60)
    assert second.set_if_absent("claim", "second", ttl=60)
    first.delete("same")
    assert not first.has("same")
    assert second.has("same")


def test_clear_removes_only_its_namespace_in_bounded_batches(redis_scope):
    _, _, cache = redis_scope
    first, second = cache("first:"), cache("second:")
    for i in range(250):
        first.set(str(i), i)
    second.set("preserved", "other application")
    first.clear()
    assert all(not first.has(str(i)) for i in range(250))
    assert second.get("preserved") == "other application"


def test_legacy_keys_remain_readable_but_unscoped_clear_is_rejected(redis_scope, monkeypatch):
    client, root, _ = redis_scope
    module = importlib.import_module("langboard_shared.core.caching.RedisCache")
    monkeypatch.setattr(
        module, "Env", SimpleNamespace(CACHE_URL=os.environ["LANGBOARD_CACHE_TEST_REDIS_URL"], CACHE_KEY_PREFIX="")
    )
    cache = module.RedisCache()
    key = root + "legacy"
    cache.set(key, "preserved")
    assert client.get(key) == '"preserved"'
    with pytest.raises(ValueError, match="CACHE_KEY_PREFIX"):
        cache.clear()
    assert cache.get(key) == "preserved"


def test_cache_operates_with_namespace_acl_and_without_flushdb_permission(redis_scope, monkeypatch):
    client, root, _ = redis_scope
    module = importlib.import_module("langboard_shared.core.caching.RedisCache")
    username = "test-" + uuid4().hex
    prefix = root + "restricted:"
    client.execute_command(
        "ACL",
        "SETUSER",
        username,
        "reset",
        "on",
        ">disposable-test-password",
        "~" + prefix + "*",
        "+get",
        "+set",
        "+exists",
        "+del",
        "+unlink",
        "+scan",
    )
    address = urlsplit(os.environ["LANGBOARD_CACHE_TEST_REDIS_URL"])
    url = urlunsplit(address._replace(netloc=f"{username}:disposable-test-password@{address.netloc}"))
    monkeypatch.setattr(module, "Env", SimpleNamespace(CACHE_URL=url, CACHE_KEY_PREFIX=prefix))
    restricted = module.RedisCache()
    try:
        client.set(root + "foreign", "preserved")
        restricted.set("value", 1)
        assert restricted.get("value") == 1
        restricted.clear()
        assert not restricted.has("value")
        assert client.get(root + "foreign") == "preserved"
        with pytest.raises(NoPermissionError, match="permissions"):
            restricted._cache.flushdb()
    finally:
        restricted._cache.close()
        client.acl_deluser(username)


@pytest.mark.parametrize("prefix", ["*:", "[ab]:", "unsafe?", ":", "missing-colon", "x" * 128 + ":"])
def test_namespace_validation_rejects_globs_and_ambiguous_boundaries(prefix):
    from langboard_shared.Env import Env

    getter = type(Env).CACHE_KEY_PREFIX.fget
    config = SimpleNamespace(_Env__get_from_cache=lambda *_: prefix)
    with pytest.raises(ValueError, match="CACHE_KEY_PREFIX"):
        getter(config)


def test_attachment_lock_uses_the_same_instance_namespace(redis_scope, monkeypatch):
    client, root, _ = redis_scope
    module = importlib.import_module("langboard_shared.ai.BoardChatAttachment")
    monkeypatch.setattr(
        module,
        "Env",
        SimpleNamespace(
            CACHE_TYPE="redis",
            CACHE_URL=os.environ["LANGBOARD_CACHE_TEST_REDIS_URL"],
            CACHE_KEY_PREFIX=root,
            AI_REQUEST_TIMEOUT=1,
        ),
    )
    with module.lock_board_chat_attachment_ticket("fixture"):
        assert client.exists(root + "board-chat-attachment:fixture:lock")
        assert not client.exists("board-chat-attachment:fixture:lock")
    assert not client.exists(root + "board-chat-attachment:fixture:lock")
