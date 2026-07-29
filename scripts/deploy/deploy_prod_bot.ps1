param(
    [string]$HostName = "138.2.57.139",
    [string]$User = "ubuntu",
    [string]$KeyPath = "C:\Users\syoub\.ssh\ssh-key-2026-06-17.key",
    [string]$ProjectDir = "/home/ubuntu/ichiyon-robot",
    [string[]]$Services = @("bot", "bot-irsia"),
    [switch]$IncludeAdmin,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

if ($IncludeAdmin -and -not ($Services -contains "admin")) {
    $Services += "admin"
}

$serviceText = ($Services -join " ")
$mode = if ($Execute) { "execute" } else { "dry-run" }

$remote = @"
set -eu
cd "$ProjectDir"
echo "deploy_mode=$mode"
echo "services=$serviceText"
echo "head=\$(git rev-parse HEAD)"
echo "branch=\$(git branch --show-current)"
echo "status_begin"
git status --short
echo "status_end"
echo "compose_diff_stat_begin"
git diff --stat -- docker-compose.yml || true
echo "compose_diff_stat_end"
echo "stash_count=\$(git stash list | wc -l | tr -d ' ')"
echo "ps_before_begin"
docker compose ps
echo "ps_before_end"
if [ "$mode" = "execute" ]; then
  git fetch origin
  git merge --ff-only origin/main
  docker compose --profile bot --profile irsia --profile youtube-vpn up -d --build $serviceText
  echo "ps_after_begin"
  docker compose ps
  echo "ps_after_end"
else
  echo "dry-run: no git merge or docker recreate executed"
fi
"@

$bytes = [System.Text.Encoding]::UTF8.GetBytes($remote)
$b64 = [Convert]::ToBase64String($bytes)
ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 -i $KeyPath "$User@$HostName" "echo $b64 | base64 -d | bash"
