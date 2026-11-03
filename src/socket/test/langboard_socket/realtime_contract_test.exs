defmodule LangboardSocket.RealtimeContractTest do
  use ExUnit.Case, async: true

  alias LangboardSocket.RealtimeContract

  test "client command inventory is read from the shared contract" do
    assert RealtimeContract.client_command?("board", "board:chat:send")
    assert RealtimeContract.client_command?("none", "board:card:editor:copilot:abort")
    refute RealtimeContract.client_command?("board", "unknown")
    refute RealtimeContract.client_command?("unknown", "board:chat:send")
  end

  test "broker ownership is read from the shared contract" do
    assert RealtimeContract.broker_schema_version!() == "2"
    assert RealtimeContract.broker_event!("fanout") == "socket_publish"
    assert RealtimeContract.dead_letter_schema_version!() == "1"
    assert RealtimeContract.dead_letter_topic!("fanout") == "socket_publish_dead_letter"
  end

  test "protocol limits are read from the shared contract" do
    assert RealtimeContract.protocol_limit!("max_topic_ids") == 64
    assert RealtimeContract.protocol_limit!("max_topic_id_bytes") == 128
  end

  test "approval edits retain exact fields and bounded identities" do
    edit = %{"plan_id" => "plan", "args_hash" => String.duplicate("a", 64), "tool_args" => %{}}
    assert RealtimeContract.valid_approval_edit?(edit)

    for invalid <- [
          nil,
          false,
          [],
          Map.delete(edit, "plan_id"),
          Map.put(edit, "api_key", "forged"),
          Map.put(edit, "plan_id", ""),
          Map.put(edit, "plan_id", 1),
          Map.put(edit, "args_hash", String.duplicate("a", 63)),
          Map.put(edit, "args_hash", String.duplicate("a", 65)),
          Map.put(edit, "tool_args", [])
        ] do
      refute RealtimeContract.valid_approval_edit?(invalid)
    end
  end

  test "internal API compatibility is read from the shared contract" do
    assert RealtimeContract.internal_api_capabilities_path!() == "/auth/socket/capabilities"
    assert RealtimeContract.internal_api_contract_version!() == 1
  end
end
