$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "run_docker_compose.ps1") "ollama-gpu"
exit $LASTEXITCODE
