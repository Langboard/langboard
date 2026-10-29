defmodule LangboardSocket.Broker.Kafka.TransportTest do
  use ExUnit.Case, async: false
  alias LangboardSocket.Broker.Kafka.Transport

  defp options(values) do
    Transport.from_env!(fn name ->
      Map.get(values, String.replace_prefix(name, "BROADCAST_KAFKA_", ""))
    end)
  end

  test "bundled default leaves native plaintext options intact" do
    assert options(%{}) == []
    assert Transport.options([]) == []
  end

  test "TLS verifies peer and discovered broker hostname with OS trust" do
    ssl = options(%{"SECURITY_PROTOCOL" => "SSL"}) |> Keyword.fetch!(:ssl)
    assert ssl[:verify] == :verify_peer
    assert is_list(ssl[:cacerts]) and ssl[:cacerts] != []
    assert is_function(ssl[:customize_hostname_check][:match_fun], 2)
    refute Keyword.has_key?(ssl, :server_name_indication)
  end

  test "native SASL mechanisms use a credential-free native callback" do
    for {name, atom} <- [
          {"PLAIN", :plain},
          {"SCRAM-SHA-256", :scram_sha_256},
          {"SCRAM-SHA-512", :scram_sha_512}
        ],
        protocol <- ["SASL_SSL", "SASL_PLAINTEXT"] do
      config =
        options(%{
          "SECURITY_PROTOCOL" => protocol,
          "SASL_MECHANISM" => name,
          "SASL_USERNAME" => "user",
          "SASL_PASSWORD" => "secret-password"
        })

      assert {:callback, Transport, ^atom} = config[:sasl]
      refute inspect(config) =~ "user"
      refute inspect(config) =~ "secret-password"
      assert Keyword.has_key?(config, :ssl) == (protocol == "SASL_SSL")
    end
  end

  test "locked Broadway schema accepts native SSL and SASL callback options" do
    config =
      options(%{
        "SECURITY_PROTOCOL" => "SASL_SSL",
        "SASL_MECHANISM" => "PLAIN",
        "SASL_USERNAME" => "user",
        "SASL_PASSWORD" => "secret-password"
      })

    assert {:ok, validated} =
             NimbleOptions.validate(
               [
                 hosts: [{"broker.invalid", 9093}],
                 group_id: "fixture",
                 topics: ["socket_publish"],
                 client_config: config
               ],
               BroadwayKafka.ProducerOptions.schema()
             )

    assert validated[:client_config][:sasl] == {:callback, Transport, :plain}
  end

  defp set_env(values) do
    previous = Map.new(values, fn {key, _value} -> {key, System.get_env(key)} end)
    System.put_env(values)

    on_exit(fn ->
      Enum.each(previous, fn {key, value} ->
        if value == nil, do: System.delete_env(key), else: System.put_env(key, value)
      end)
    end)
  end

  test "native callback authentication failure returns no server or credential details" do
    set_env(%{
      "BROADCAST_KAFKA_SASL_USERNAME" => "private-user",
      "BROADCAST_KAFKA_SASL_PASSWORD" => "private-password"
    })

    log =
      ExUnit.CaptureLog.capture_log(fn ->
        assert {:error, :authentication_failed} =
                 :kpro_auth_backend.auth(
                   Transport,
                   ~c"broker.invalid",
                   :invalid_socket,
                   1,
                   :gen_tcp,
                   "fixture",
                   1,
                   :plain
                 )
      end)

    refute log =~ "private-user"
    refute log =~ "private-password"
  end

  test "runtime reads TLS files only when Kafka is enabled" do
    set_env(%{
      "SOCKET_PHOENIX_KAFKA_ENABLED" => "false",
      "BROADCAST_KAFKA_SECURITY_PROTOCOL" => "SSL",
      "BROADCAST_KAFKA_SSL_CAFILE" => "/missing/private-ca.pem"
    })

    runtime = Path.expand("../../../../config/runtime.exs", __DIR__)
    config = Config.Reader.read!(runtime, env: :test)
    assert config[:langboard_socket][:kafka][:client_config] == []
    System.put_env("SOCKET_PHOENIX_KAFKA_ENABLED", "true")

    assert_raise ArgumentError,
                 "Kafka TLS trust or client certificate configuration is invalid",
                 fn ->
                   Config.Reader.read!(runtime, env: :test)
                 end
  end

  test "inconsistent options fail closed with safe errors" do
    for values <- [
          %{"SECURITY_PROTOCOL" => "secret-invalid"},
          %{"SSL_CAFILE" => "secret-path"},
          %{"SASL_PASSWORD" => "secret-password"},
          %{"SECURITY_PROTOCOL" => "SSL", "SSL_CERTFILE" => "secret-path"},
          %{"SECURITY_PROTOCOL" => "SSL", "SSL_CAFILE" => "secret-path"},
          %{
            "SECURITY_PROTOCOL" => "SASL_SSL",
            "SASL_MECHANISM" => "secret-invalid",
            "SASL_USERNAME" => "secret-user",
            "SASL_PASSWORD" => "secret-password"
          }
        ] do
      error = assert_raise ArgumentError, fn -> options(values) end
      refute Exception.message(error) =~ "secret"
    end
  end

  test "invalid CA PEM fails before any broker connection" do
    path =
      Path.join(System.tmp_dir!(), "langboard-kafka-ca-#{System.unique_integer([:positive])}.pem")

    File.write!(path, "invalid private material")
    on_exit(fn -> File.rm(path) end)

    assert_raise ArgumentError,
                 "Kafka TLS trust or client certificate configuration is invalid",
                 fn ->
                   options(%{"SECURITY_PROTOCOL" => "SSL", "SSL_CAFILE" => path})
                 end
  end
end
