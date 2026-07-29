param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [string]$HostName = "141.147.145.113",
    [string]$User = "ubuntu",
    [string]$KeyPath = "C:\Users\syoub\.ssh\ssh-key-2026-06-20.key",
    [string]$ProjectDir = "/home/ubuntu/minecraft-bedrock",
    [string]$Service = "bedrock",
    [switch]$Apply,
    [switch]$ConfirmWorldReplace
)

$ErrorActionPreference = "Stop"

$RemoteScript = @"
set -eu
cd "$ProjectDir"
echo "project=$ProjectDir"
echo "service=$Service"
echo "backup=$BackupPath"
echo "apply=$([bool]$Apply)"
echo "confirm_world_replace=$([bool]$ConfirmWorldReplace)"
if [ "$([bool]$Apply)" != "True" ] || [ "$([bool]$ConfirmWorldReplace)" != "True" ]; then
  echo "dry_run=true"
  echo "restore requires both -Apply and -ConfirmWorldReplace"
  echo "would_stop_service=$Service"
  echo "would_move_current_data_to_timestamped_backup"
  echo "would_extract_backup=$BackupPath"
  echo "would_restart_service=$Service"
  exit 0
fi
test -f "$BackupPath"
ts=`$(date +%Y%m%d-%H%M%S)
current_backup="$ProjectDir/backups/pre-restore-current-data-\$ts"
mkdir -p "$ProjectDir/backups"
docker compose stop "$Service"
mv data "\$current_backup"
tar -xzf "$BackupPath"
docker compose up -d "$Service"
docker compose ps "$Service"
echo "restore_done=true"
echo "previous_data=\$current_backup"
"@

$RemoteScript = $RemoteScript -replace "`r", ""
$EncodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteScript))
ssh -o ConnectTimeout=60 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KeyPath "$User@$HostName" "echo '$EncodedScript' | base64 -d | bash"
