from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print("[{0}] {1}{2}".format(status, name, " - {0}".format(detail) if detail else ""))
    return bool(condition)


def main():
    prod = (ROOT / "scripts" / "deploy" / "deploy_prod_bot.ps1").read_text(encoding="utf-8")
    stg = (ROOT / "scripts" / "deploy" / "deploy_stg_bot.ps1").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "operations" / "deployment-standard.md").read_text(encoding="utf-8")
    combined = prod + "\n" + stg
    results = []
    results.append(check("prod script uses ssh", "ssh " in prod and "138.2.57.139" in prod))
    results.append(check("stg script uses safe ssh options", "ConnectTimeout=60" in stg and "141.147.145.113" in stg))
    results.append(check("scripts do not use docker compose down", "docker compose down" not in combined))
    results.append(check("scripts do not use git reset hard", "reset --hard" not in combined))
    results.append(check("scripts preserve stash", "stash pop" not in combined and "stash apply" not in combined and "stash drop" not in combined))
    results.append(check("scripts recreate explicit services only", '@("bot", "bot-irsia")' in prod and '@("bot", "bot-irsia")' in stg))
    results.append(check("docs list rollback checks", "rollback判断材料" in docs or "復旧判断材料" in docs or "rollback" in docs.lower()))
    ok = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok, len(results)))
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
