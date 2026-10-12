defmodule LangboardSocketWeb.SocketHandler.CardAudience do
  @moduledoc false

  alias LangboardSocket.RealtimeContract, as: Contract

  def authorize(payload, state) do
    topic = payload["topic"]
    topic_id = payload["topic_id"]
    event = payload["event"] || ""
    data = payload["data"] || %{}
    references = Map.get(payload, "card_uids", [])

    references =
      if topic == Contract.topic!("board_card"), do: [topic_id | references], else: references

    references =
      case data do
        %{"card" => %{"uid" => uid}} when is_binary(uid) -> [uid | references]
        _ -> references
      end

    references = Enum.uniq(references)

    protected =
      references != [] or Regex.match?(~r/^(board:card:|dashboard:(card|checkitem):)/, event)

    removal = Regex.match?(~r/^(board|dashboard):card:deleted:/, event)

    cond do
      not protected ->
        case state.authorization_client.authorize_subscriptions(
               state.authorization_token,
               topic,
               [topic_id]
             ) do
          {:ok, accepted} ->
            if topic_id in accepted, do: {:ok, scrub(payload, removal)}, else: :revoked

          {:error, reason} ->
            {:error, reason}
        end

      references == [] or length(references) > 2 ->
        {:ok, nil}

      true ->
        case state.authorization_client.authorize_subscriptions(
               state.authorization_token,
               topic,
               [topic_id],
               card_uids: references,
               card_operation: if(removal, do: "remove", else: "read")
             ) do
          {:ok, accepted} ->
            if topic_id in accepted do
              {:ok, scrub(payload, removal)}
            else
              # Distinguish a hidden card from revoked board membership only on denial.
              case state.authorization_client.authorize_subscriptions(
                     state.authorization_token,
                     topic,
                     [topic_id]
                   ) do
                {:ok, current} -> if topic_id in current, do: {:ok, nil}, else: :revoked
                {:error, reason} -> {:error, reason}
              end
            end

          {:error, reason} ->
            {:error, reason}
        end
    end
  end

  defp scrub(payload, removal) do
    payload = Map.delete(payload, "card_uids")

    if removal,
      do:
        Map.put(
          payload,
          "data",
          Map.take(payload["data"] || %{}, ["uid", "project_column_uid", "source_type"])
        ),
      else: payload
  end
end
