param(
    [string]$HostName = "141.147.145.113",
    [string]$User = "ubuntu",
    [string]$KeyPath = "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key",
    [string]$ProjectDir = "/home/ubuntu/minecraft-bedrock",
    [string]$Service = "bedrock",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$RemoteScript = @'
set -eu
PROJECT_DIR="__PROJECT_DIR__"
SERVICE="__SERVICE__"
APPLY="__APPLY__"
cd "$PROJECT_DIR"
ts=$(date +%Y%m%d-%H%M%S)
backup_dir="$PROJECT_DIR/backups"
backup_file="$backup_dir/bedrock-backup-$ts.tar.gz"
echo "project=$PROJECT_DIR"
echo "service=$SERVICE"
echo "apply=$APPLY"
echo "backup_file=$backup_file"
if [ "$APPLY" != "True" ]; then
  echo "dry_run=true"
  echo "would_stop_service=$SERVICE"
  echo "would_archive=data docker-compose.yml"
  echo "would_restart_service=$SERVICE"
  exit 0
fi
mkdir -p "$backup_dir"
docker compose ps "$SERVICE"
docker compose stop "$SERVICE"
tar -czf "$backup_file" docker-compose.yml data
docker compose up -d "$SERVICE"
docker compose ps "$SERVICE"
echo "backup_done=$backup_file"
'@

$RemoteScript = $RemoteScript.Replace("__PROJECT_DIR__", $ProjectDir)
$RemoteScript = $RemoteScript.Replace("__SERVICE__", $Service)
$RemoteScript = $RemoteScript.Replace("__APPLY__", [string][bool]$Apply)
$RemoteScript = $RemoteScript -replace "`r", ""
$EncodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteScript))
ssh -o ConnectTimeout=60 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KeyPath "$User@$HostName" "echo '$EncodedScript' | base64 -d | bash"
