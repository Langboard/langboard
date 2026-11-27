#!/bin/bash

set -e

# Load .env file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

VAULT_URL=${KEY_PROVIDER_OPENBAO_URL:-"http://127.0.0.1:8200"}
ROOT_TOKEN=${KEY_PROVIDER_OPENBAO_ROOT_TOKEN:-}
PROJECT_NAME=${PROJECT_NAME:-"langboard"}
ROLE_NAME="${PROJECT_NAME}-role"
CREDENTIALS_FILE=".vault-credentials"

echo "OpenBao Local Initialization Script (HTTP API)"
echo "=========================================================="
echo "Vault Address: $VAULT_URL"
echo ""

# Skip initialization when local OpenBao is not configured.
if [ -z "$ROOT_TOKEN" ]; then
    echo "INFO: KEY_PROVIDER_OPENBAO_ROOT_TOKEN is not set; skipping OpenBao initialization"
    exit 0
fi

echo "Checking if OpenBao is running..."
HEALTH_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "$VAULT_URL/v1/sys/health" || true)"
if [ "$HEALTH_STATUS" != "200" ] && [ "$HEALTH_STATUS" != "429" ]; then
    if [ -z "$HEALTH_STATUS" ]; then
        HEALTH_STATUS="000"
    fi
    echo "INFO: OpenBao is not running at $VAULT_URL (status: $HEALTH_STATUS); skipping initialization"
    exit 0
fi
echo "OpenBao is running"
echo ""

# Enable AppRole auth method if not already enabled
echo "Configuring AppRole authentication..."
APPROLE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    "$VAULT_URL/v1/sys/auth/approle")

if [ "$APPROLE_STATUS" = "200" ]; then
    echo "AppRole already enabled"
else
    curl -s -X POST \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        -d '{"type": "approle"}' \
        "$VAULT_URL/v1/sys/auth/approle" > /dev/null
    echo "AppRole enabled"
fi
echo ""

# Create policy for apikeys
echo "Creating policy for apikeys..."
curl -s -X POST \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"policy":"path \"apikeys/data/*\" {\n  capabilities = [\"create\", \"update\", \"read\"]\n}\n\npath \"apikeys/metadata/*\" {\n  capabilities = [\"create\", \"update\", \"delete\", \"list\"]\n}\n\npath \"apikeys/delete/*\" {\n  capabilities = [\"update\"]\n}"}' \
    "$VAULT_URL/v1/sys/policy/apikeys-policy" > /dev/null
echo "Policy created"
echo ""

# Create or update the role
echo "Creating/updating AppRole: $ROLE_NAME"
curl -s -X POST \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"token_policies\": \"apikeys-policy\"}" \
    "$VAULT_URL/v1/auth/approle/role/$ROLE_NAME" > /dev/null
echo "Role created/updated"
echo ""

# Get role_id
echo "Retrieving Role ID..."
ROLE_ID=$(curl -s \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    "$VAULT_URL/v1/auth/approle/role/$ROLE_NAME/role-id" | \
    grep -o '"role_id":"[^"]*"' | \
    cut -d'"' -f4)
echo "   Role ID: $ROLE_ID"
echo ""

# Generate secret_id
echo "Generating Secret ID..."
SECRET_ID=$(curl -s -X POST \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    "$VAULT_URL/v1/auth/approle/role/$ROLE_NAME/secret-id" | \
    grep -o '"secret_id":"[^"]*"' | \
    cut -d'"' -f4)
echo "   Secret ID: $SECRET_ID"
echo ""

# Enable KV v2 secrets engine for apikeys if not already enabled
echo "Configuring KV v2 secrets engine..."
KV_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    "$VAULT_URL/v1/sys/mounts/apikeys")

if [ "$KV_STATUS" = "200" ]; then
    echo "KV v2 secrets engine already mounted at apikeys/"
else
    curl -s -X POST \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        -d '{"type": "kv-v2"}' \
        "$VAULT_URL/v1/sys/mounts/apikeys" > /dev/null
    echo "KV v2 secrets engine mounted at apikeys/"
fi
echo ""

# Write credentials to .vault-credentials file
echo "Writing credentials to $CREDENTIALS_FILE..."
cat > "$CREDENTIALS_FILE" << EOF
VAULT_ROLE_ID=$ROLE_ID
VAULT_SECRET_ID=$SECRET_ID
EOF

chmod 600 "$CREDENTIALS_FILE"
echo "Credentials file created"
echo ""

# Test the credentials
echo "Testing AppRole authentication..."
TEST_RESPONSE=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "{\"role_id\": \"$ROLE_ID\", \"secret_id\": \"$SECRET_ID\"}" \
    "$VAULT_URL/v1/auth/approle/login")

if echo "$TEST_RESPONSE" | grep -q '"client_token"'; then
    echo "AppRole authentication successful"
else
    echo "AppRole authentication failed"
    echo "   Response: $TEST_RESPONSE"
    exit 1
fi
echo ""

# Restore keys from vault-data directory if exists
RESTORE_DIR="$PROJECT_ROOT/local/vault-data"
if [ -d "$RESTORE_DIR" ]; then
    echo "Checking for keys to restore from $RESTORE_DIR..."

    KEY_COUNT=$(find "$RESTORE_DIR" -type f | wc -l)

    if [ "$KEY_COUNT" -gt 0 ]; then
        echo "Found $KEY_COUNT key file(s) to restore..."
        echo ""

        CLIENT_TOKEN=$(echo "$TEST_RESPONSE" | grep -o '"client_token":"[^"]*"' | cut -d'"' -f4)

        RESTORED=0
        SKIPPED=0
        FAILED=0

        for KEY_FILE in "$RESTORE_DIR"/*; do
            if [ -f "$KEY_FILE" ]; then
                KEY_ID=$(basename "$KEY_FILE")
                KEY_MATERIAL=$(cat "$KEY_FILE")

                echo "   Restoring key: $KEY_ID..."

                CHECK_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
                    -H "X-Vault-Token: $CLIENT_TOKEN" \
                    "$VAULT_URL/v1/apikeys/data/$KEY_ID")

                if [ "$CHECK_STATUS" = "200" ]; then
                    echo "   Skipped (already exists): $KEY_ID"
                    SKIPPED=$((SKIPPED + 1))
                else
                    curl -s -X POST \
                        -H "X-Vault-Token: $CLIENT_TOKEN" \
                        -H "Content-Type: application/json" \
                        -d "{\"data\": {\"key_material\": \"$KEY_MATERIAL\"}}" \
                        "$VAULT_URL/v1/apikeys/data/$KEY_ID" > /dev/null

                    if [ $? -eq 0 ]; then
                        echo "   Restored: $KEY_ID"
                        RESTORED=$((RESTORED + 1))
                    else
                        echo "   Failed to restore: $KEY_ID"
                        FAILED=$((FAILED + 1))
                    fi
                fi
            fi
        done

        echo ""
        echo "Restore summary:"
        echo "   Restored: $RESTORED"
        echo "   Skipped (already exists): $SKIPPED"
        echo "   Failed: $FAILED"
        echo ""
    else
        echo "No key files found in $RESTORE_DIR"
        echo ""
    fi
else
    echo "No vault-data directory found (skipping restore)"
    echo ""
fi

echo "=========================================================="
echo "OpenBao initialization complete"
echo ""
echo "Credentials saved to: $CREDENTIALS_FILE"
echo ""
echo "OpenBao configuration:"
echo "   - AppRole: $ROLE_NAME"
echo "   - KV v2 secrets: apikeys/"
echo "   - Vault Address: $VAULT_URL"
echo ""
