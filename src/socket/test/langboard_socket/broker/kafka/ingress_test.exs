defmodule LangboardSocket.Broker.Kafka.IngressTest do
  use ExUnit.Case, async: true

  alias Broadway.Message
  alias LangboardSocket.Broker.Kafka.Ingress, as: KafkaIngress
  alias LangboardSocket.SubscriptionTopic

  test "projects a v2 Kafka record to the matching PubSub subscription" do
    Phoenix.PubSub.subscribe(
      LangboardSocket.PubSub,
      SubscriptionTopic.name("board", "board-1")
    )

    message =
      message(%{
        schema_version: "2",
        event_id: "event-1",
        event: "socket_publish",
        occurred_at: "2026-09-12T12:00:00Z",
        data: %{
          data: %{card: %{uid: "card-1"}},
          publish_models: %{
            topic: "board",
            topic_id: "board-1",
            event: "board:card:updated",
            data_keys: "card"
          }
        }
      })

    assert %Message{status: :ok} = KafkaIngress.handle_message(:default, message, context())

    assert_receive {:socket_event,
                    %{
                      "topic" => "board",
                      "topic_id" => "board-1",
                      "event" => "board:card:updated",
                      "data" => %{"card" => %{"uid" => "card-1"}}
                    }}
  end

  test "marks malformed records as failed" do
    invalid = message(%{cache_key: "legacy-only"})

    assert %Message{status: {:failed, :invalid_broker_envelope}} =
             KafkaIngress.handle_message(:default, invalid, context())
  end

  test "dead-letters rejected records with bounded inspection data" do
    parent = self()

    publisher = fn record, 250 ->
      send(parent, {:dead_letter, record})
      :ok
    end

    failed =
      KafkaIngress.handle_message(
        :default,
        message(%{cache_key: "invalid-key"}, %{partition: 2, offset: 41}),
        context()
      )

    assert [%Message{metadata: %{dead_letter_persisted: true}}] =
             KafkaIngress.handle_failed(
               [failed],
               context(%{
                 dead_letter_publisher: publisher,
                 dead_letter_max_payload_bytes: 8
               })
             )

    assert_receive {:dead_letter,
                    %{
                      "schema_version" => "1",
                      "reason" => "invalid_broker_envelope",
                      "source" => %{
                        "topic" => "socket_publish",
                        "partition" => 2,
                        "offset" => 41
                      },
                      "payload_bytes" => payload_bytes,
                      "payload_base64" => retained_payload,
                      "payload_truncated" => true,
                      "payload_sha256" => payload_sha256
                    }}

    assert payload_bytes > 8
    assert byte_size(Base.decode64!(retained_payload)) == 8
    assert byte_size(payload_sha256) == 64
  end

  test "bounds dead-letter retries" do
    {:ok, attempts} = Agent.start_link(fn -> 0 end)

    publisher = fn _record, _timeout_ms ->
      Agent.get_and_update(attempts, fn count ->
        {{:error, :dead_letter_unavailable}, count + 1}
      end)
    end

    failed =
      KafkaIngress.handle_message(
        :default,
        message(%{cache_key: "invalid-key"}),
        context()
      )

    assert [^failed] =
             KafkaIngress.handle_failed(
               [failed],
               context(%{dead_letter_publisher: publisher})
             )

    assert Agent.get(attempts, & &1) == 3
  end

  defp context(overrides \\ %{}) do
    Map.merge(
      %{
        dead_letter_publisher: fn _record, _timeout_ms -> :ok end,
        dead_letter_topic: "socket_publish_dead_letter",
        dead_letter_max_payload_bytes: 64,
        dead_letter_publish_timeout_ms: 250,
        processing_max_attempts: 3,
        processing_retry_backoff_ms: 0
      },
      overrides
    )
  end

  defp message(data, metadata \\ %{}) do
    %Message{
      data: Jason.encode!(data),
      metadata: Map.merge(%{topic: "socket_publish"}, metadata),
      acknowledger: {Broadway.NoopAcknowledger, nil, nil}
    }
  end
end
