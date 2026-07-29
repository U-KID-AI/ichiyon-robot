param(
    [string]$HostName = "141.147.145.113",
    [string]$User = "ubuntu",
    [string]$KeyPath = "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key",
    [string]$ProjectDir = "/home/ubuntu/minecraft-bedrock",
    [string]$Service = "bedrock",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$RemoteScript = @"
set -eu
cd "$ProjectDir"
ts=`$(date +%Y%m%d-%H%M%S)
backup_dir="$ProjectDir/backups"
backup_file="\$backup_dir/bedrock-backup-\$ts.tar.gz"
echo "project=$ProjectDir"
echo "service=$Service"
echo "mode=$([bool]$Apply)"
echo "backup_file=\$backup_file"
if [ "$([bool]$Apply)" != "True" ]; then
  echo "dry_run=true"
  echo "would_stop_service=$Service"
  echo "would_archive=data docker-compose.yml"
  echo "would_restart_service=$Service"
  exit 0
fi
mkdir -p "\$backup_dir"
docker compose ps "$Service"
docker compose stop "$Service"
tar -czf "\$backup_file" docker-compose.yml data
docker compose up -d "$Service"
docker compose ps "$Service"
echo "backup_done=\$backup_file"
"@

$RemoteScript = $RemoteScript -replace "`r", ""
$EncodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteScript))
ssh -o ConnectTimeout=60 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KeyPath "$User@$HostName" "echo '$EncodedScript' | base64 -d | bash"
