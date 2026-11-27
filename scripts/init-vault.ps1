$ErrorActionPreference = "Stop"

# Load .env file
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $ProjectRoot ".env"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

$VaultAddr = if ($env:KEY_PROVIDER_OPENBAO_URL) { $env:KEY_PROVIDER_OPENBAO_URL } else { "http://127.0.0.1:8200" }
$ProjectName = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "langboard" }
$RoleName = "${ProjectName}-role"
$CredentialsFile = ".vault-credentials"

Write-Host "OpenBao Local Initialization Script (HTTP API)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Vault Address: $VaultAddr"
Write-Host ""

if (-not $env:KEY_PROVIDER_OPENBAO_ROOT_TOKEN) {
    Write-Host "INFO: KEY_PROVIDER_OPENBAO_ROOT_TOKEN is not set; skipping OpenBao initialization" -ForegroundColor Yellow
    exit 0
}

Write-Host "Checking if OpenBao is running..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$VaultAddr/v1/sys/health" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -notin @(200, 429)) {
        throw "OpenBao not responding correctly"
    }
    Write-Host "OpenBao is running" -ForegroundColor Green
} catch {
    Write-Host "INFO: OpenBao is not running at $VaultAddr; skipping initialization" -ForegroundColor Yellow
    exit 0
}
Write-Host ""

$headers = @{
    "X-Vault-Token" = $env:KEY_PROVIDER_OPENBAO_ROOT_TOKEN
}

Write-Host "Configuring AppRole authentication..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$VaultAddr/v1/sys/auth/approle" -Headers $headers -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host "AppRole already enabled" -ForegroundColor Green
    }
} catch {
    try {
        $body = @{
            type = "approle"
        } | ConvertTo-Json

        Invoke-RestMethod -Uri "$VaultAddr/v1/sys/auth/approle" -Method Post -Headers $headers -Body $body -ContentType "application/json" | Out-Null
        Write-Host "AppRole enabled" -ForegroundColor Green
    } catch {
        Write-Host "Error enabling AppRole: $_" -ForegroundColor Red
        exit 1
    }
}
Write-Host ""

Write-Host "Creating policy for apikeys..." -ForegroundColor Yellow
try {
    $policy = @"
path "apikeys/data/*" {
  capabilities = ["create", "update", "read"]
}

path "apikeys/metadata/*" {
  capabilities = ["create", "update", "delete", "list"]
}

path "apikeys/delete/*" {
  capabilities = ["update"]
}
"@

    $policyBody = @{
        policy = $policy
    } | ConvertTo-Json

    Invoke-RestMethod -Uri "$VaultAddr/v1/sys/policy/apikeys-policy" -Method Post -Headers $headers -Body $policyBody -ContentType "application/json" | Out-Null
    Write-Host "Policy created" -ForegroundColor Green
} catch {
    Write-Host "Error creating policy: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Creating/updating AppRole: $RoleName" -ForegroundColor Yellow
try {
    $body = @{
        token_policies = "apikeys-policy"
    } | ConvertTo-Json

    Invoke-RestMethod -Uri "$VaultAddr/v1/auth/approle/role/$RoleName" -Method Post -Headers $headers -Body $body -ContentType "application/json" | Out-Null
    Write-Host "Role created/updated" -ForegroundColor Green
} catch {
    Write-Host "Error creating role: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Retrieving Role ID..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "$VaultAddr/v1/auth/approle/role/$RoleName/role-id" -Headers $headers -Method Get
    $RoleId = $response.data.role_id
    Write-Host "   Role ID: $RoleId" -ForegroundColor White
} catch {
    Write-Host "Error retrieving role ID: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Generating Secret ID..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "$VaultAddr/v1/auth/approle/role/$RoleName/secret-id" -Headers $headers -Method Post
    $SecretId = $response.data.secret_id
    Write-Host "   Secret ID: $SecretId" -ForegroundColor White
} catch {
    Write-Host "Error generating secret ID: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Configuring KV v2 secrets engine..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$VaultAddr/v1/sys/mounts/apikeys" -Headers $headers -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host "KV v2 secrets engine already mounted at apikeys/" -ForegroundColor Green
    }
} catch {
    try {
        $body = @{
            type = "kv-v2"
        } | ConvertTo-Json

        Invoke-RestMethod -Uri "$VaultAddr/v1/sys/mounts/apikeys" -Method Post -Headers $headers -Body $body -ContentType "application/json" | Out-Null
        Write-Host "KV v2 secrets engine mounted at apikeys/" -ForegroundColor Green
    } catch {
        Write-Host "Error enabling KV v2: $_" -ForegroundColor Red
        exit 1
    }
}
Write-Host ""

Write-Host "Writing credentials to $CredentialsFile..." -ForegroundColor Yellow
try {
    @"
VAULT_ROLE_ID=$RoleId
VAULT_SECRET_ID=$SecretId
"@ | Out-File -FilePath $CredentialsFile -Encoding ASCII

    $acl = Get-Acl $CredentialsFile
    $acl.SetAccessRuleProtection($true, $false)
    $accessRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME,
        "Read,Write",
        "None",
        "None",
        "Allow"
    )
    $acl.SetAccessRule($accessRule)
    Set-Acl $CredentialsFile $acl

    Write-Host "Credentials file created" -ForegroundColor Green
} catch {
    Write-Host "Error writing credentials file: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Testing AppRole authentication..." -ForegroundColor Yellow
try {
    $testBody = @{
        role_id = $RoleId
        secret_id = $SecretId
    } | ConvertTo-Json

    $testResponse = Invoke-RestMethod -Uri "$VaultAddr/v1/auth/approle/login" -Method Post -Body $testBody -ContentType "application/json"

    if ($testResponse.auth.client_token) {
        Write-Host "AppRole authentication successful" -ForegroundColor Green
    } else {
        throw "Authentication failed - no token in response"
    }
} catch {
    Write-Host "AppRole authentication failed" -ForegroundColor Red
    Write-Host "   $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

$RestoreDir = Join-Path $ProjectRoot "local\vault-data"
if (Test-Path $RestoreDir) {
    Write-Host "Checking for keys to restore from $RestoreDir..." -ForegroundColor Yellow

    $KeyFiles = Get-ChildItem -Path $RestoreDir -File

    if ($KeyFiles.Count -gt 0) {
        Write-Host "Found $($KeyFiles.Count) key file(s) to restore..." -ForegroundColor Cyan
        Write-Host ""

        $ClientToken = $testResponse.auth.client_token
        $restoreHeaders = @{
            "X-Vault-Token" = $ClientToken
        }

        $Restored = 0
        $Skipped = 0
        $Failed = 0

        foreach ($KeyFile in $KeyFiles) {
            $KeyId = $KeyFile.Name
            $KeyMaterial = Get-Content $KeyFile -Raw

            Write-Host "   Restoring key: $KeyId..." -ForegroundColor White

            try {
                $checkResponse = Invoke-WebRequest -Uri "$VaultAddr/v1/apikeys/data/$KeyId" -Headers $restoreHeaders -UseBasicParsing -TimeoutSec 5

                if ($checkResponse.StatusCode -eq 200) {
                    Write-Host "   Skipped (already exists): $KeyId" -ForegroundColor DarkYellow
                    $Skipped++
                }
            } catch {
                try {
                    $restoreBody = @{
                        data = @{
                            key_material = $KeyMaterial.Trim()
                        }
                    } | ConvertTo-Json

                    Invoke-RestMethod -Uri "$VaultAddr/v1/apikeys/data/$KeyId" -Method Post -Headers $restoreHeaders -Body $restoreBody -ContentType "application/json" | Out-Null
                    Write-Host "   Restored: $KeyId" -ForegroundColor Green
                    $Restored++
                } catch {
                    Write-Host "   Failed to restore: $KeyId" -ForegroundColor Red
                    Write-Host "      Error: $_" -ForegroundColor DarkRed
                    $Failed++
                }
            }
        }

        Write-Host ""
        Write-Host "Restore summary:" -ForegroundColor Cyan
        Write-Host "   Restored: $Restored" -ForegroundColor Green
        Write-Host "   Skipped (already exists): $Skipped" -ForegroundColor DarkYellow
        Write-Host "   Failed: $Failed" -ForegroundColor Red
        Write-Host ""
    } else {
        Write-Host "No key files found in $RestoreDir" -ForegroundColor Gray
        Write-Host ""
    }
} else {
    Write-Host "No vault-data directory found (skipping restore)" -ForegroundColor Gray
    Write-Host ""
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "OpenBao initialization complete" -ForegroundColor Green
Write-Host ""
Write-Host "Credentials saved to: $CredentialsFile" -ForegroundColor White
Write-Host ""
Write-Host "OpenBao configuration:" -ForegroundColor White
Write-Host "   - AppRole: $RoleName" -ForegroundColor White
Write-Host "   - KV v2 secrets: apikeys/" -ForegroundColor White
Write-Host "   - OpenBao Address: $VaultAddr" -ForegroundColor White
Write-Host ""
