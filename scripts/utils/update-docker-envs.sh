#!/bin/bash

# Source utility functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

CURRENT_DIR=$(basename "$PWD")

if [[ "$CURRENT_DIR" == "langboard" && -d "./docker" ]]; then
    cd docker
elif [[ "$CURRENT_DIR" == "docker" ]]; then
    :
else
    echo "You must run this script from the langboard root directory or the docker directory."
    exit 1
fi

cd ../

source .env

# MAIL_SERVER is optional: leave empty to disable mail features. When set
# later, fill it in .env and re-run this script (or make update_docker_settings).
if [ -z "$MAIL_SERVER" ]; then
    echo "MAIL_SERVER is empty — outbound mail is disabled. Set it in .env to enable."
fi

# Validate external postgres URLs if they are set
if [ -n "$POSTGRES_EXTERNAL_MAIN_URL" ]; then
    validate_postgres_url "$POSTGRES_EXTERNAL_MAIN_URL" "POSTGRES_EXTERNAL_MAIN_URL"

    if [ -n "$POSTGRES_EXTERNAL_REPLICA_URL" ]; then
        validate_postgres_url "$POSTGRES_EXTERNAL_REPLICA_URL" "POSTGRES_EXTERNAL_REPLICA_URL"
    fi
fi

# Set OpenBao URL if using openbao-local
if [[ "$KEY_PROVIDER_TYPE" == "openbao-local" ]]; then
    export KEY_PROVIDER_OPENBAO_URL="http://${PROJECT_NAME}_vault:8200"
    echo "OpenBao URL set to: $KEY_PROVIDER_OPENBAO_URL"
fi

declare -A service_envs=(
  [nginx]="server-common"
  [api]="server-common server"
  [ui]="server-common"
  [socket]="server-common server"
  [flows]="server-common server"
  [db_backup]="db-backup"
)

echo ${service_envs[@]}

for service in "${!service_envs[@]}"; do
  output_file="docker/envs/.${service}.env"
  echo "Generating $output_file from templates: ${service_envs[$service]}"

  : > "$output_file"

  for template in ${service_envs[$service]}; do
    template_path="docker/envs/${template}.env.template"

    if [[ -f "$template_path" ]]; then
      eval "$(cat "$template_path")"
      while read -r line; do
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
          var_name="${BASH_REMATCH[1]}"
          echo "$var_name=${!var_name}" >> "$output_file"
        fi
      done < "$template_path"
    else
      echo "⚠️ Warning: template '$template_path' not found"
    fi
  done
done