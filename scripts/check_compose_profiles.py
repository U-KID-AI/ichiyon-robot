from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print("[{0}] {1}{2}".format(status, name, " - {0}".format(detail) if detail else ""))
    return bool(condition)


def main():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    stg = (ROOT / "docker-compose.stg.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "operations" / "production-compose-differences.md").read_text(encoding="utf-8")
    results = []
    results.append(check("bot profile exists", "- bot" in compose))
    results.append(check("irsia profile exists", "- irsia" in compose))
    results.append(check("youtube vpn profile exists", "- youtube-vpn" in compose))
    results.append(check("voicevox profile exists", "- voicevox" in compose))
    results.append(check("prod overlay keeps prod container names", "ichiyon-robot-prod-admin" in prod and "ichiyon-robot-prod-bot" in prod))
    results.append(check("stg overlay keeps stg container names", "ichiyon-robot-stg-admin" in stg and "ichiyon-robot-stg-bot" in stg))
    results.append(check("docs classify production compose differences", "production固有の正当な差分" in docs))
    results.append(check("docs forbid destructive deploy actions", "docker compose down" in docs and "volume削除は禁止" in docs))
    ok = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok, len(results)))
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
