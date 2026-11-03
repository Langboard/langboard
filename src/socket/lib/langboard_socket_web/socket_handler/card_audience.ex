defmodule LangboardSocketWeb.SocketHandler.CardAudience do
  @moduledoc false

  alias LangboardSocket.RealtimeContract, as: Contract

  def authorize(payload, state) do
    topic = payload["topic"]
    topic_id = payload["topic_id"]
    event = payload["event"] || ""
    references = card_references(payload, topic, topic_id)

    protected =
      references != [] or Regex.match?(~r/^(board:card:|dashboard:(card|checkitem):)/, event)

    removal = Regex.match?(~r/^(board|dashboard):card:deleted:/, event)

    cond do
      not protected -> authorize_topic(payload, state, removal, :deliver)
      references == [] or length(references) > 2 -> {:ok, nil}
      true -> authorize_cards(payload, state, references, removal)
    end
  end

  defp card_references(payload, topic, topic_id) do
    data = payload["data"] || %{}
    references = Map.get(payload, "card_uids", [])

    references =
      if topic == Contract.topic!("board_card"), do: [topic_id | references], else: references

    references =
      case data do
        %{"card" => %{"uid" => uid}} when is_binary(uid) -> [uid | references]
        _ -> references
      end

    Enum.uniq(references)
  end

  defp authorize_cards(payload, state, references, removal) do
    case state.authorization_client.authorize_subscriptions(
           state.authorization_token,
           payload["topic"],
           [payload["topic_id"]],
           card_uids: references,
           card_operation: if(removal, do: "remove", else: "read")
         ) do
      {:ok, accepted} -> card_result(payload, state, accepted, removal)
      {:error, reason} -> {:error, reason}
    end
  end

  defp card_result(payload, state, accepted, removal) do
    if payload["topic_id"] in accepted do
      {:ok, scrub(payload, removal)}
    else
      # Distinguish a hidden card from revoked board membership only on denial.
      authorize_topic(payload, state, removal, :hide)
    end
  end

  defp authorize_topic(payload, state, removal, action) do
    case state.authorization_client.authorize_subscriptions(
           state.authorization_token,
           payload["topic"],
           [payload["topic_id"]]
         ) do
      {:ok, accepted} -> topic_result(payload, accepted, removal, action)
      {:error, reason} -> {:error, reason}
    end
  end

  defp topic_result(payload, accepted, removal, action) do
    if payload["topic_id"] in accepted,
      do: accepted_payload(payload, removal, action),
      else: :revoked
  end

  defp accepted_payload(payload, removal, :deliver), do: {:ok, scrub(payload, removal)}
  defp accepted_payload(_payload, _removal, :hide), do: {:ok, nil}

  defp scrub(payload, removal) do
    payload = payload |> Map.delete("card_uids") |> scrub_wiki_notification()

    if removal,
      do:
        Map.put(
          payload,
          "data",
          Map.take(payload["data"] || %{}, ["uid", "project_column_uid", "source_type"])
        ),
      else: payload
  end

  defp scrub_wiki_notification(
         %{
           "event" => "user:notified",
           "data" => %{"notification" => %{"type" => "mentioned_in_wiki"} = notification}
         } = payload
       ) do
    # Already queued envelopes can predate the producer's invalidation-only contract.
    put_in(payload, ["data", "notification"], Map.take(notification, ["uid", "type"]))
  end

  defp scrub_wiki_notification(payload), do: payload
end
