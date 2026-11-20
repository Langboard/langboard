import ssl
from pathlib import Path
import pytest
from langboard_shared.core.broadcast.kafka.Transport import transport_options


def options(protocol="PLAINTEXT", **values):
    return transport_options(
        {
            "BROADCAST_KAFKA_SECURITY_PROTOCOL": protocol,
            **{f"BROADCAST_KAFKA_{key}": value for key, value in values.items()},
        }
    )


def test_plaintext_default_is_native_compatible():
    assert transport_options({}) == {"security_protocol": "PLAINTEXT"}


@pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
def test_tls_requires_peer_and_hostname_verification(protocol):
    config = options(
        protocol,
        **(
            {"SASL_MECHANISM": "PLAIN", "SASL_USERNAME": "user", "SASL_PASSWORD": "secret"}
            if protocol.startswith("SASL")
            else {}
        ),
    )
    assert config["ssl_check_hostname"] is True
    assert config["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert config["ssl_context"].check_hostname is True
    assert config["ssl_context"].get_ca_certs()


@pytest.mark.parametrize("mechanism", ["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"])
@pytest.mark.parametrize("protocol", ["SASL_PLAINTEXT", "SASL_SSL"])
def test_sasl_native_option_names(protocol, mechanism):
    config = options(protocol, SASL_MECHANISM=mechanism, SASL_USERNAME="user", SASL_PASSWORD="secret")
    assert config["sasl_mechanism"] == mechanism
    assert config["sasl_plain_username"] == "user"
    assert config["sasl_plain_password"] == "secret"


@pytest.mark.parametrize(
    "protocol,values",
    [
        ("secret-invalid-protocol", {}),
        ("PLAINTEXT", {"SSL_CAFILE": "secret-path"}),
        ("PLAINTEXT", {"SASL_PASSWORD": "secret-password"}),
        ("SSL", {"SSL_CERTFILE": "secret-path"}),
        ("SSL", {"SSL_CAFILE": "secret-path"}),
        (
            "SASL_SSL",
            {"SASL_MECHANISM": "secret-invalid", "SASL_USERNAME": "secret-user", "SASL_PASSWORD": "secret-password"},
        ),
        ("SASL_SSL", {"SASL_MECHANISM": "PLAIN", "SASL_USERNAME": "secret-user"}),
    ],
)
def test_inconsistent_options_fail_closed_without_echoing_values(protocol, values):
    with pytest.raises(ValueError) as error:
        options(protocol, **values)
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__ or error.value.__context__ is None


def test_explicit_trust_is_loaded_and_invalid_pem_is_rejected(tmp_path):
    pem = tmp_path / "ca.pem"
    pem.write_text("invalid secret certificate")
    with pytest.raises(ValueError, match="Kafka TLS trust"):
        options("SSL", SSL_CAFILE=str(pem))
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile:
        config = options("SSL", SSL_CAFILE=defaults.cafile)
        assert config["ssl_context"].get_ca_certs()


def test_publisher_preserves_native_transport_and_delivery_guarantees(monkeypatch):
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import Mock

    module = import_module("langboard_shared.core.broadcast.kafka.KafkaDispatcherQueue")
    config = options("SASL_SSL", SASL_MECHANISM="SCRAM-SHA-256", SASL_USERNAME="user", SASL_PASSWORD="secret")
    env = SimpleNamespace(
        BROADCAST_URLS=["broker.invalid:9093"],
        BROADCAST_KAFKA_CONFIG=config,
        BROADCAST_MAX_MESSAGE_BYTES=10485760,
        BROADCAST_PUBLISH_TIMEOUT_SECONDS=7,
    )
    constructor = Mock()
    monkeypatch.setattr(module, "Env", env)
    monkeypatch.setattr(module, "KafkaProducer", constructor)
    module.KafkaDispatcherQueue().put("socket_publish", {})
    passed = constructor.call_args.kwargs
    assert passed["ssl_context"] is config["ssl_context"]
    assert passed["security_protocol"] == "SASL_SSL"
    assert passed["sasl_plain_password"] == "secret"
    assert passed["enable_idempotence"] is True
    assert passed["max_request_size"] == 10485760
    constructor.return_value.send.return_value.get.assert_called_once_with(timeout=7)


@pytest.mark.parametrize(
    "script,function",
    [
        ("bootstrap-phoenix-kafka-group.py", "require_existing_group"),
        ("check-phoenix-cutover.py", "check_phoenix_fanout_caught_up"),
    ],
)
def test_maintenance_clients_share_tls_and_keep_committed_offsets(monkeypatch, script, function):
    import runpy
    from types import SimpleNamespace
    from unittest.mock import Mock
    from kafka.structs import OffsetAndMetadata, TopicPartition

    root = Path(__file__).resolve().parents[4]
    target = runpy.run_path(str(root / "scripts" / script))[function]
    config = options("SSL")
    monkeypatch.setitem(
        target.__globals__,
        "Env",
        SimpleNamespace(BROADCAST_URLS=["broker.invalid:9093"], BROADCAST_KAFKA_CONFIG=config),
    )
    monkeypatch.setenv("BROADCAST_PHOENIX_FANOUT_CONSUMER_GROUP", "fixture")
    partition = TopicPartition("socket_publish", 0)
    admin, consumer = Mock(), Mock()
    admin.describe_consumer_groups.return_value = [SimpleNamespace(state="Stable")]
    admin.list_consumer_group_offsets.return_value = {partition: OffsetAndMetadata(5, "", -1)}
    consumer.partitions_for_topic.return_value = {0}
    consumer.beginning_offsets.return_value = {partition: 0}
    consumer.end_offsets.return_value = {partition: 5}
    admin_constructor, consumer_constructor = Mock(return_value=admin), Mock(return_value=consumer)
    monkeypatch.setitem(target.__globals__, "KafkaAdminClient", admin_constructor)
    monkeypatch.setitem(target.__globals__, "KafkaConsumer", consumer_constructor)
    target("fixture") if function == "require_existing_group" else target()
    for constructor in (admin_constructor, consumer_constructor):
        assert constructor.call_args.kwargs["ssl_context"] is config["ssl_context"]
        assert constructor.call_args.kwargs["security_protocol"] == "SSL"
    assert consumer_constructor.call_args.kwargs["enable_auto_commit"] is False
    consumer.commit.assert_not_called()
    admin.close.assert_called_once()
    consumer.close.assert_called_once()


def test_actual_native_log_emitters_redact_sasl_wire_records_and_credentials(caplog):
    import logging
    from kafka.protocol.sasl_authenticate import SaslAuthenticateRequest

    options("SASL_SSL", SASL_MECHANISM="PLAIN", SASL_USERNAME="private-user", SASL_PASSWORD="private-password")
    caplog.set_level(logging.DEBUG, logger="kafka.conn")
    caplog.set_level(logging.DEBUG, logger="kafka.protocol.parser")
    request = SaslAuthenticateRequest[0](b"\x00private-user\x00private-password")
    logging.getLogger("kafka.protocol.parser").debug("Sending request %s", request)
    logging.getLogger("kafka.conn").debug("Request %d: %s", 1, request)
    logging.getLogger("kafka.conn").info("Authenticated as %s via SASL / Plain", "private-user")
    logging.getLogger("kafka.conn").error("SaslAuthenticate error: %s", "broker echoed private-password")
    try:
        raise RuntimeError("broker echoed private-user private-password")
    except RuntimeError:
        logging.getLogger("kafka.conn").exception("Connection failed for %s", "private-user")
    logging.getLogger("kafka.conn").warning("Connection timed out after %d ms", 100)
    assert "private-user" not in caplog.text
    assert "private-password" not in caplog.text
    assert "auth_bytes=" not in caplog.text
    assert "Kafka authentication diagnostic (content omitted)" in caplog.text
    assert "Connection timed out after 100 ms" in caplog.text
