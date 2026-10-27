defmodule LangboardSocket.Broker.Kafka.Transport do
  @moduledoc false

  # kpro supplies the actual broker hostname as SNI for every discovered broker.
  # ssl: true in the locked kpro version disables verification; never use it.
  def from_env!(get_env \\ &System.get_env/1) do
    get = fn suffix -> get_env.("BROADCAST_KAFKA_" <> suffix) || "" end
    protocol = get_env.("BROADCAST_KAFKA_SECURITY_PROTOCOL") || "PLAINTEXT"
    tls? = protocol in ["SSL", "SASL_SSL"]
    sasl? = protocol in ["SASL_PLAINTEXT", "SASL_SSL"]
    ca = get.("SSL_CAFILE")
    cert = get.("SSL_CERTFILE")
    key = get.("SSL_KEYFILE")
    mechanism = get.("SASL_MECHANISM")
    username = get.("SASL_USERNAME")
    password = get.("SASL_PASSWORD")

    unless protocol in ["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"],
      do: raise(ArgumentError, "BROADCAST_KAFKA_SECURITY_PROTOCOL is unsupported")

    if not tls? and Enum.any?([ca, cert, key], &(&1 != "")),
      do: raise(ArgumentError, "Kafka TLS files require SSL or SASL_SSL")

    if cert == "" != (key == ""),
      do: raise(ArgumentError, "Kafka TLS client certificate and key must be configured together")

    if not sasl? and Enum.any?([mechanism, username, password], &(&1 != "")),
      do: raise(ArgumentError, "Kafka SASL settings require a SASL security protocol")

    mechanisms = %{
      "PLAIN" => :plain,
      "SCRAM-SHA-256" => :scram_sha_256,
      "SCRAM-SHA-512" => :scram_sha_512
    }

    if sasl? and (not Map.has_key?(mechanisms, mechanism) or username == "" or password == ""),
      do: raise(ArgumentError, "Kafka SASL requires a supported mechanism, username and password")

    ssl = if tls?, do: [ssl: ssl_options!(ca, cert, key)], else: []

    # Broadway validates password tuples as binaries. Its native callback hook
    # keeps credentials out of stored client configuration and validation errors.
    if sasl?,
      do: ssl ++ [sasl: {:callback, __MODULE__, Map.fetch!(mechanisms, mechanism)}],
      else: ssl
  end

  # Delegate the complete handshake and PLAIN/SCRAM exchange to locked kpro.
  # Return a stable error rather than a broker-controlled message or stack trace.
  def auth(host, socket, handshake_version, transport, client_id, timeout, mechanism) do
    credentials =
      {mechanism, System.fetch_env!("BROADCAST_KAFKA_SASL_USERNAME"),
       System.fetch_env!("BROADCAST_KAFKA_SASL_PASSWORD")}

    :kpro_sasl.auth(host, socket, transport, client_id, timeout, credentials, handshake_version)
  rescue
    _error -> {:error, :authentication_failed}
  catch
    _kind, _reason -> {:error, :authentication_failed}
  end

  def options(config), do: Keyword.get(config, :client_config, [])

  defp ssl_options!(ca, cert, key) do
    trust =
      if ca == "" do
        [cacerts: :public_key.cacerts_get()]
      else
        validate_pem!(ca, :ca)
        [cacertfile: String.to_charlist(ca)]
      end

    identity =
      if cert == "" do
        []
      else
        validate_pem!(cert, :certificate)
        validate_pem!(key, :key)
        [certfile: String.to_charlist(cert), keyfile: String.to_charlist(key)]
      end

    [
      verify: :verify_peer,
      customize_hostname_check: [match_fun: :public_key.pkix_verify_hostname_match_fun(:https)]
    ] ++ trust ++ identity
  rescue
    _error ->
      raise ArgumentError, "Kafka TLS trust or client certificate configuration is invalid"
  end

  defp validate_pem!(path, kind) do
    entries = path |> File.read!() |> :public_key.pem_decode()

    valid? =
      Enum.any?(entries, fn
        {:Certificate, _, :not_encrypted} when kind in [:ca, :certificate] ->
          true

        {type, _, :not_encrypted} when kind == :key ->
          type in [:RSAPrivateKey, :ECPrivateKey, :PrivateKeyInfo]

        _entry ->
          false
      end)

    unless valid?, do: raise(ArgumentError, "Invalid Kafka TLS PEM file")
  end
end
