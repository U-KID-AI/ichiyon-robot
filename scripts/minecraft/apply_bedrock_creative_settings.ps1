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
config_backup="$backup_dir/config-before-creative-$ts"
world_backup="$backup_dir/world-before-creative-$ts.tar.gz"

echo "project=$PROJECT_DIR"
echo "service=$SERVICE"
echo "apply=$APPLY"

python3 - <<'PY'
from pathlib import Path
path = Path("docker-compose.yml")
text = path.read_text(encoding="utf-8")
required_pairs = {
    "GAMEMODE": "creative",
    "FORCE_GAMEMODE": '"true"',
    "DIFFICULTY": "peaceful",
    "ALLOW_CHEATS": '"true"',
    "MAX_PLAYERS": '"10"',
    "IMMUTABLE_WORLD": '"false"',
    "ONLINE_MODE": '"true"',
    "DEFAULT_PLAYER_PERMISSION_LEVEL": "member",
    "VIEW_DISTANCE": '"16"',
    "TICK_DISTANCE": '"4"',
}
for key, value in required_pairs.items():
    print(f"target_{key}={value}")
PY

if [ "$APPLY" != "True" ]; then
  echo "dry_run=true"
  echo "would_backup_config=$config_backup"
  echo "would_backup_world=$world_backup"
  echo "would_stop_service=$SERVICE"
  echo "would_update_compose_environment=creative_settings"
  echo "would_start_service=$SERVICE"
  exit 0
fi

mkdir -p "$config_backup"
cp docker-compose.yml "$config_backup/docker-compose.yml"
if [ -f data/server.properties ]; then
  cp data/server.properties "$config_backup/server.properties"
fi

docker compose stop "$SERVICE"
tar -czf "$world_backup" docker-compose.yml data

python3 - <<'PY'
from pathlib import Path

path = Path("docker-compose.yml")
lines = path.read_text(encoding="utf-8").splitlines()
updates = {
    "GAMEMODE": "creative",
    "FORCE_GAMEMODE": '"true"',
    "DIFFICULTY": "peaceful",
    "ALLOW_CHEATS": '"true"',
    "MAX_PLAYERS": '"10"',
    "IMMUTABLE_WORLD": '"false"',
    "ONLINE_MODE": '"true"',
    "DEFAULT_PLAYER_PERMISSION_LEVEL": "member",
    "VIEW_DISTANCE": '"16"',
    "TICK_DISTANCE": '"4"',
}
found = set()
out = []
env_indent = None
insert_at = None
in_environment = False
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == "environment:":
        env_indent = len(line) - len(line.lstrip(" "))
        in_environment = True
        insert_at = len(out) + 1
        out.append(line)
        continue
    if in_environment:
        indent = len(line) - len(line.lstrip(" "))
        if stripped and indent <= env_indent:
            for key, value in updates.items():
                if key not in found:
                    out.append(" " * (env_indent + 2) + f"{key}: {value}")
                    found.add(key)
            in_environment = False
        else:
            for key, value in updates.items():
                if stripped.startswith(f"{key}:"):
                    line = " " * indent + f"{key}: {value}"
                    found.add(key)
                    break
    out.append(line)
if in_environment:
    for key, value in updates.items():
        if key not in found:
            out.append(" " * (env_indent + 2) + f"{key}: {value}")
            found.add(key)

path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

docker compose config --quiet
docker compose up -d "$SERVICE"
sleep 8
docker compose ps "$SERVICE"
docker inspect -f 'restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' minecraft-bedrock-stg || true
ss -lunp | grep -E '19132|19133' || true
if [ -f data/server.properties ]; then
  grep -E '^(level-name|gamemode|force-gamemode|difficulty|allow-cheats|max-players|online-mode|view-distance|tick-distance|default-player-permission-level|immutable-world)=' data/server.properties || true
fi
echo "config_backup=$config_backup"
echo "world_backup=$world_backup"
'@

$RemoteScript = $RemoteScript.Replace("__PROJECT_DIR__", $ProjectDir)
$RemoteScript = $RemoteScript.Replace("__SERVICE__", $Service)
$RemoteScript = $RemoteScript.Replace("__APPLY__", [string][bool]$Apply)
$RemoteScript = $RemoteScript -replace "`r", ""
$EncodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteScript))
ssh -o ConnectTimeout=60 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KeyPath "$User@$HostName" "echo '$EncodedScript' | base64 -d | bash"
