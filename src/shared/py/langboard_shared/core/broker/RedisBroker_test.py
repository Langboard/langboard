"""Exercise Celery's configured Kombu Redis transport against a disposable server."""

import importlib
import os
from queue import Empty
from types import SimpleNamespace
from uuid import uuid4
import pytest
from redis import Redis


@pytest.fixture
def brokers(monkeypatch):
    url = os.environ.get("LANGBOARD_CACHE_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set LANGBOARD_CACHE_TEST_REDIS_URL to a disposable Redis server")
    module = importlib.import_module("langboard_shared.core.broker.Broker")
    root = f"test:{uuid4().hex}:"
    apps = []
    for suffix in ("first:", "second:"):
        monkeypatch.setattr(
            module,
            "Env",
            SimpleNamespace(
                CACHE_TYPE="redis",
                BROKER_URL=url,
                PROJECT_NAME="langboard-test",
                BROKER_KEY_PREFIX=root + suffix,
            ),
        )
        apps.append(type(module.Broker)().celery)
    client = Redis.from_url(url, decode_responses=True)
    yield apps, client, root
    for app in apps:
        app.close()
    for key in client.scan_iter(match=root + "*"):
        client.delete(key)
    client.close()


def test_same_queue_name_is_isolated_by_native_transport_prefix(brokers):
    apps, client, root = brokers
    with apps[0].connection_for_write() as first, apps[1].connection_for_write() as second:
        with first.SimpleQueue("same-queue") as first_queue, second.SimpleQueue("same-queue") as second_queue:
            first_queue.put({"owner": "first"})
            with pytest.raises(Empty):
                second_queue.get(block=False)
            second_queue.put({"owner": "second"})
            for queue, owner in ((first_queue, "first"), (second_queue, "second")):
                message = queue.get(block=False)
                assert message.payload == {"owner": owner}
                message.ack()
            keys = list(client.scan_iter(match=root + "*"))
            assert any(key.startswith(root + "first:") for key in keys)
            assert any(key.startswith(root + "second:") for key in keys)


def test_native_pubsub_channels_are_prefixed_and_do_not_cross_instances(brokers):
    apps, _, _ = brokers
    with apps[0].connection_for_write() as first, apps[1].connection_for_write() as second:
        first_client, second_client = first.channel().client, second.channel().client
        with first_client.pubsub() as first_sub, second_client.pubsub() as second_sub:
            first_sub.subscribe("same-control")
            second_sub.subscribe("same-control")
            assert first_sub.get_message(timeout=1)["type"] == "subscribe"
            assert second_sub.get_message(timeout=1)["type"] == "subscribe"
            assert first_client.publish("same-control", "first") == 1
            assert first_sub.get_message(timeout=1)["data"] == b"first"
            assert second_sub.get_message(timeout=0.1) is None
