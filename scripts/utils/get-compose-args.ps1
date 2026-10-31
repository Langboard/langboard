param(
    [string]$WithDocs = "false",
    [string]$WithUiWatcher = "false",
    [string]$WithOllamaCpu = "false",
    [string]$WithOllamaGpu = "false",
    [string]$WithOtel = "false",
    [string]$WithDbBackup = "true"
)

# Get Docker Compose arguments based on environment variables
# Usage: .\scripts\get-compose-args.ps1

# Keep an explicitly supplied trace sink ahead of an empty .env default.
$traceEndpointFromEnvironment = $env:SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT

# Load .env file
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

if (-not [string]::IsNullOrEmpty($traceEndpointFromEnvironment)) {
    $env:SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT = $traceEndpointFromEnvironment
}

$env:WITH_DOCS = $WithDocs
$env:WITH_UI_WATCHER = $WithUiWatcher
$env:WITH_OLLAMA_CPU = $WithOllamaCpu
$env:WITH_OLLAMA_GPU = $WithOllamaGpu
$env:WITH_OTEL = $WithOtel
$env:WITH_DB_BACKUP = $WithDbBackup

# Function to validate PostgreSQL URL
function Test-PostgresUrl {
    param(
        [string]$Url,
        [string]$UrlName
    )

    if (-not [string]::IsNullOrEmpty($Url)) {
        # Check if URL matches postgresql:// or postgres:// format
        if ($Url -notmatch '^postgres(ql)?://') {
            [Console]::Error.WriteLine("Error: $UrlName must start with postgresql:// or postgres://")
            exit 1
        }

        # Basic structure check: postgresql://user:pass@host:port/db
        if ($Url -notmatch '^postgres(ql)?://[^:]+(:[^@]+)?@[^:]+:[0-9]+/.+$') {
            [Console]::Error.WriteLine("Warning: $UrlName may not be in correct format (postgresql://user:password@host:port/database)")
            [Console]::Error.Write("Continue anyway? (y/N) ")
            $response = [Console]::ReadLine()
            if ($response -ne 'y' -and $response -ne 'Y') {
                exit 1
            }
        }

        [Console]::Error.WriteLine("✓ $UrlName is valid")
    }
}

# Validate external postgres URLs if they are set
if (-not [string]::IsNullOrEmpty($env:POSTGRES_EXTERNAL_MAIN_URL)) {
    Test-PostgresUrl -Url $env:POSTGRES_EXTERNAL_MAIN_URL -UrlName "POSTGRES_EXTERNAL_MAIN_URL"

    if (-not [string]::IsNullOrEmpty($env:POSTGRES_EXTERNAL_REPLICA_URL)) {
        Test-PostgresUrl -Url $env:POSTGRES_EXTERNAL_REPLICA_URL -UrlName "POSTGRES_EXTERNAL_REPLICA_URL"
    }
}

$COMPOSE_PREFIX = "./docker/docker-compose"

# Base compose args (include the main compose file)
$COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.yaml -f ${COMPOSE_PREFIX}.kafka.yaml"

# Check if we should use external postgres (skip postgres compose files)
if ([string]::IsNullOrEmpty($env:POSTGRES_EXTERNAL_MAIN_URL)) {
    $COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.pg.yaml"
}

# Backup service is enabled by default
$withDbBackup = if ([string]::IsNullOrEmpty($env:WITH_DB_BACKUP)) { "true" } else { $env:WITH_DB_BACKUP.ToLowerInvariant() }
if ($withDbBackup -eq "true") {
    $COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.backup.yaml"
}

$COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.redis.yaml -f ${COMPOSE_PREFIX}.server.yaml"

if ($env:REDIS_EXTERNAL_CACHE_URL -or $env:REDIS_EXTERNAL_BROKER_URL) {
    foreach ($requiredName in @("REDIS_EXTERNAL_CACHE_URL", "REDIS_EXTERNAL_BROKER_URL", "CACHE_KEY_PREFIX", "BROKER_KEY_PREFIX")) {
        if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($requiredName))) {
            [Console]::Error.WriteLine("$requiredName is required for external Redis")
            exit 1
        }
    }
    $COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.redis-external.yaml"
}
# External realtime is an explicit deployment choice, never an endpoint fallback.
$socketMode = if ([string]::IsNullOrEmpty($env:SOCKET_DEPLOYMENT_MODE)) { "bundled" } else { $env:SOCKET_DEPLOYMENT_MODE.ToLowerInvariant() }
if ($socketMode -notin @("bundled", "external")) {
    [Console]::Error.WriteLine("SOCKET_DEPLOYMENT_MODE must be bundled or external")
    exit 1
}
if ($socketMode -eq "external") {
    foreach ($name in @("SOCKET_URL", "SOCKET_INTERNAL_URL", "SOCKET_EDITOR_INTERNAL_URL")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        $schemes = if ($name -eq "SOCKET_URL") { "https?|wss?" } else { "https?" }
        $pattern = "^($schemes)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(:[0-9]+)?(/[^\s?#\\]*)?$"
        if ([string]::IsNullOrEmpty($value) -or $value -cnotmatch $pattern) {
            [Console]::Error.WriteLine("$name must be an explicit compatible service URL without credentials, query or fragment")
            exit 1
        }
    }
    $COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.phoenix-external.yaml"
}
$COMPOSE_ARGS += " --env-file ./.env"

# Optional compose args
$VAULT_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.vault.yaml"
$DOCS_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.docs.yaml"
$UI_WATCHER_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.ui-watcher.yaml"
$OLLAMA_SHARED_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.ollama.shared.yaml"
$OLLAMA_CPU_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.ollama.cpu.yaml ${OLLAMA_SHARED_COMPOSE_ARGS}"
$OLLAMA_GPU_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.ollama.gpu.yaml ${OLLAMA_SHARED_COMPOSE_ARGS}"
$OTEL_COMPOSE_ARGS = "-f ${COMPOSE_PREFIX}.otel.yaml --profile socket-phoenix --profile socket-phoenix-otel"

# Append optional compose args based on environment variables
if ($env:WITH_DOCS -eq "true") {
    $COMPOSE_ARGS += " ${DOCS_COMPOSE_ARGS}"
}

if ($env:WITH_UI_WATCHER -eq "true") {
    $COMPOSE_ARGS += " ${UI_WATCHER_COMPOSE_ARGS}"
}

if ($env:WITH_OLLAMA_CPU -eq "true") {
    $COMPOSE_ARGS += " ${OLLAMA_CPU_COMPOSE_ARGS}"
}

if ($env:WITH_OLLAMA_GPU -eq "true") {
    $COMPOSE_ARGS += " ${OLLAMA_GPU_COMPOSE_ARGS}"
}

if ($env:WITH_OTEL -eq "true" -and $socketMode -eq "bundled") {
    $COMPOSE_ARGS += " ${OTEL_COMPOSE_ARGS}"
}

if ($env:WITH_OTEL -eq "true" -and $socketMode -eq "bundled" -and -not [string]::IsNullOrEmpty($env:SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT)) {
    $COMPOSE_ARGS += " -f ${COMPOSE_PREFIX}.phoenix-otel-traces.yaml"
}

# Match the runtime default and compare the resolved value, not a file prefix.
if ([string]::IsNullOrEmpty($env:KEY_PROVIDER_TYPE) -or $env:KEY_PROVIDER_TYPE -eq "openbao-local") {
    $COMPOSE_ARGS += " ${VAULT_COMPOSE_ARGS}"
}

# Native commands require each Compose argument as a separate array item.
Write-Output ($COMPOSE_ARGS -split ' ')
