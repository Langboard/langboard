# Vault provider dependencies

The default API and shared package install no longer includes Azure Identity or
Azure Key Vault. Azure deployments must select the `azure-vault` extra explicitly:

```sh
uv sync --locked --no-dev --extra azure-vault
# Shared library consumers:
pip install 'langboard-shared[azure-vault]'
```

For the repository container, choose its provider image target:

```sh
docker build --target with-azure-vault -t langboard-azure .
```

Set `KEY_PROVIDER_TYPE=azure` and configure the existing Azure credentials after
installing this extra. A selected provider with missing dependencies still fails
startup; it does not silently fall back or change encryption/key formats. Provider
selection remains deployment-owned. API users cannot select it through request
arguments. OpenBao/HashiCorp dependencies remain in the default package for the
default production vault. AWS/S3 is optional:

```sh
uv sync --locked --no-dev --extra aws
pip install 'langboard-shared[aws]'
docker build --target with-aws -t langboard-aws .
```

Install `aws` for S3 storage or `KEY_PROVIDER_TYPE=aws`. Local file operations do
not import the AWS SDK. Existing S3 objects retain their `s3` namespace; missing
SDKs do not reinterpret them as local files. S3 operation failures retain the
existing false/None results and upload connection fallback. Selecting the AWS
production vault without its SDK still fails startup. No runtime download occurs.

The `with-document-processing` and `with-cron` targets retain their existing
behavior. Deployments combining Azure with those features should install the
`azure-vault` or `aws` extra in their derived image too; runtime provider selection does
not download packages. No cloud account, organization, domain, or credential is
embedded in the optional package metadata.

## Bundled or external key management

Provider selection is instance deployment configuration, not a per-board preference
or an MCP argument. Use the same provider and credentials for every API/worker that
resolves the instance's SecretRefs. The bundled OpenBao service is selected only
for `KEY_PROVIDER_TYPE=openbao-local` (also the default when omitted). An external
provider does not start the bundled vault; existing volumes are not deleted.

| Provider | Configuration | Storage behavior |
| --- | --- | --- |
| Bundled OpenBao | `openbao-local` | KV v2 secrets in the bundled vault; mounted `.vault-credentials` remains supported. |
| External OpenBao | `openbao-external`, `KEY_PROVIDER_OPENBAO_URL`, `KEY_PROVIDER_OPENBAO_ROLE_ID`, `KEY_PROVIDER_OPENBAO_SECRET_ID` | KV v2 secrets in the selected server. |
| HashiCorp Vault | `hashicorp`, `KEY_PROVIDER_HASHICORP_URL`, `KEY_PROVIDER_HASHICORP_ROLE_ID`, `KEY_PROVIDER_HASHICORP_SECRET_ID` | KV v2 secrets in the selected server. |
| AWS KMS | `aws`, existing `KEY_PROVIDER_AWS_*` variables | External encryption; ciphertext/envelopes remain in the existing Langboard records. This is not AWS Secrets Manager. |
| Azure Key Vault | `azure`, existing `KEY_PROVIDER_AZURE_*` variables | External key encryption; ciphertext/envelopes remain in the existing Langboard records. This is not a remote KV secret implementation. |

For OpenBao, an explicit AppRole pair takes precedence over the legacy mounted
`.vault-credentials` file. A partial pair fails before authentication, without
falling back to the file. Existing external deployments using that file continue
to work. Supply bootstrap credentials using the deployment's secret injection;
do not put values in a wiki, card, public repository, log, or ordinary settings
response. A SecretRef cannot bootstrap the vault that must resolve that same ref.

Both OpenBao and HashiCorp clients keep TLS verification enabled. Set
`KEY_PROVIDER_VAULT_CA_CERT` to a CA bundle **path inside each consuming container**
for private certificate authorities; mount that file read-only with your deployment
override. An empty value uses trusted system CAs. There is no TLS-disable option.
`KEY_PROVIDER_VAULT_TIMEOUT` defaults to 30 seconds and accepts 1–120 seconds.
External OpenBao key generation does not create the legacy plaintext development
key copy. That copy is retained only for explicit `openbao-local` development.
These use the existing [official hvac client options](https://python-hvac.org/en/stable/source/hvac_v1.html).

The existing KV v2 mount is `apikeys`. Provision a separate vault or a restricted
AppRole and namespace of secret paths per instance; a naming prefix alone is not
an authorization boundary. Limit read/write/delete to the instance's paths. Runtime
clients do not need root tokens or permission to enable engines or alter policies.

### Changing providers safely

Saving a provider selection does not migrate secrets, rewrite SecretRefs, reindex
attachments, delete old keys, or remove volumes. Existing locators depend on the
old vault or encryption key. Back up the database and the relevant vault/key
configuration before an explicit migration. Retain the previous provider and keys
until all references have been migrated and verified; keep the old configuration
available for rollback. Do not point a live instance at an empty replacement vault.

Before promotion, verify a disposable SecretRef's create → resolve → rotate →
resolve after restart → revoke flow through the deployed API/worker. Also exercise
expired credentials, rejected CA chains, insufficient permissions, unreachable
servers, and cross-instance denial. Unit tests and Compose rendering are not proof
of a successful connection to an operator's external server. Production startup
fails when the selected provider cannot initialize; only the existing development
`openbao-local` fallback may use local files.
