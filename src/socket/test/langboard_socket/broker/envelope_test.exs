defmodule LangboardSocket.Broker.EnvelopeTest do
  use ExUnit.Case, async: true

  alias LangboardSocket.Broker.Envelope

  test "decodes the current inline envelope" do
    payload =
      Jason.encode!(%{
        schema_version: "2",
        event_id: "event-1",
        event: "socket_publish",
        occurred_at: "2026-09-12T12:00:00Z",
        data: %{value: "ok"}
      })

    assert {:ok, envelope} = Envelope.decode(payload, "socket_publish")
    assert envelope.event_id == "event-1"
    assert envelope.data == %{"value" => "ok"}
  end

  test "rejects a cache pointer without an inline envelope" do
    assert {:error, :invalid_broker_envelope} =
             Envelope.decode(Jason.encode!(%{cache_key: "broadcast-old-key"}), "socket_publish")
  end

  test "rejects an invalid versioned envelope" do
    assert {:error, :invalid_broker_envelope} =
             Envelope.decode(
               Jason.encode!(%{
                 schema_version: "invalid",
                 data: %{}
               }),
               "socket_publish"
             )
  end

  test "rejects mismatched and malformed inline envelopes" do
    assert {:error, :invalid_broker_envelope} =
             Envelope.decode(
               Jason.encode!(%{
                 schema_version: "2",
                 event_id: "event-1",
                 event: "notification_publish",
                 occurred_at: "2026-09-12T12:00:00Z",
                 data: %{}
               }),
               "socket_publish"
             )

    assert {:error, :invalid_broker_envelope} =
             Envelope.decode(
               Jason.encode!(%{
                 schema_version: "2",
                 event_id: "event-1",
                 event: "socket_publish",
                 occurred_at: "invalid",
                 data: %{}
               }),
               "socket_publish"
             )
  end
end
